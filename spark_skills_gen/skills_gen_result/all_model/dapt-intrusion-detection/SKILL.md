---
id: dapt-intrusion-detection
title: PCAP Network Statistics Extraction
version: 1.0.0
tags: [pcap, network-analysis, scapy, pandas, intrusion-detection, statistics]
---

# PCAP Network Statistics Extraction

## Overview

Extract a comprehensive set of network metrics from a `.pcap` file and write them into a structured CSV. The CSV has a fixed set of row keys; your job is to fill in the `value` column accurately using Python + Scapy.

---

## Module 1: Parsing and Core Metric Extraction

### Setup

```python
from scapy.all import rdpcap, IP, TCP, UDP, ICMP, ARP
from collections import defaultdict
import math, csv

pkts = rdpcap("packets.pcap")
```

### Protocol Counts

Count by layer presence — not by IP protocol number field alone:

```python
tcp = sum(1 for p in pkts if p.haslayer(TCP))
udp = sum(1 for p in pkts if p.haslayer(UDP))
icmp = sum(1 for p in pkts if p.haslayer(ICMP))
arp = sum(1 for p in pkts if p.haslayer(ARP))
ip_total = sum(1 for p in pkts if p.haslayer(IP))
```

> Critical: `protocol_udp` must use `p.haslayer(UDP)`, not `p[IP].proto == 17`. These can differ due to fragmentation or encapsulation. Always use layer presence checks.

### Timing

```python
timestamps = [float(p.time) for p in pkts]
timestamps.sort()
duration = timestamps[-1] - timestamps[0]

# Packets per 60s bucket
from collections import Counter
buckets = Counter(int((t - timestamps[0]) // 60) for t in timestamps)
bucket_counts = list(buckets.values())
ppm_avg = sum(bucket_counts) / len(bucket_counts)
ppm_max = max(bucket_counts)
ppm_min = min(bucket_counts)
```

### Sizes

```python
lengths = [len(p) for p in pkts]
total_bytes = sum(lengths)
avg_size = total_bytes / len(lengths)
min_size = min(lengths)
max_size = max(lengths)
```

### Shannon Entropy

```python
def entropy(values):
    counts = Counter(values)
    total = sum(counts.values())
    return -sum((c/total) * math.log2(c/total) for c in counts.values() if c > 0)

src_ips = [p[IP].src for p in pkts if p.haslayer(IP)]
dst_ips = [p[IP].dst for p in pkts if p.haslayer(IP)]
src_ports = [p[TCP].sport if p.haslayer(TCP) else p[UDP].sport
             for p in pkts if p.haslayer(TCP) or p.haslayer(UDP)]
dst_ports = [p[TCP].dport if p.haslayer(TCP) else p[UDP].dport
             for p in pkts if p.haslayer(TCP) or p.haslayer(UDP)]
```

---

## Module 2: Graph, Flows, IAT, PCR, and Flags

### Directed IP Graph

```python
edges = set()
node_set = set()
out_map = defaultdict(set)
in_map = defaultdict(set)

for p in pkts:
    if p.haslayer(IP):
        s, d = p[IP].src, p[IP].dst
        node_set.update([s, d])
        edges.add((s, d))
        out_map[s].add(d)
        in_map[d].add(s)

num_nodes = len(node_set)
num_edges = len(edges)
density = num_edges / (num_nodes * (num_nodes - 1)) if num_nodes >= 2 else 0
max_outdegree = max(len(v) for v in out_map.values())
max_indegree = max(len(v) for v in in_map.values())
```

### Inter-Arrival Times

```python
iats = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
iat_mean = sum(iats) / len(iats)
iat_var = sum((x - iat_mean)**2 for x in iats) / len(iats)  # population variance
iat_cv = math.sqrt(iat_var) / iat_mean if iat_mean > 0 else 0
```

### Producer/Consumer Ratio

```python
sent = defaultdict(int)
recv = defaultdict(int)
for p in pkts:
    if p.haslayer(IP):
        sent[p[IP].src] += len(p)
        recv[p[IP].dst] += len(p)

all_ips = node_set
producers = consumers = 0
for ip in all_ips:
    s, r = sent[ip], recv[ip]
    if s + r == 0:
        continue
    pcr = (s - r) / (s + r)
    if pcr > 0.2:
        producers += 1
    elif pcr < -0.2:
        consumers += 1
```

### Flows (5-tuple)

```python
flows = set()
for p in pkts:
    if p.haslayer(IP):
        proto = p[IP].proto
        sp = p[TCP].sport if p.haslayer(TCP) else (p[UDP].sport if p.haslayer(UDP) else None)
        dp = p[TCP].dport if p.haslayer(TCP) else (p[UDP].dport if p.haslayer(UDP) else None)
        if sp is not None:
            flows.add((p[IP].src, p[IP].dst, sp, dp, proto))

tcp_flows = sum(1 for f in flows if f[4] == 6)
udp_flows = sum(1 for f in flows if f[4] == 17)

# Bidirectional: flow AND its reverse both exist
bidir = sum(1 for (s,d,sp,dp,pr) in flows if (d,s,dp,sp,pr) in flows)
# bidir counts each direction separately — divide by 2 for unique pairs
bidirectional_flows = bidir // 2
```

> Critical: `bidirectional_flows` should count unique bidirectional pairs, not individual directions. Divide the raw symmetric count by 2.

### Analysis Flags

Base flags on computed metrics, not assumptions:

```python
# is_traffic_benign: true unless strong evidence of attack
# has_port_scan: true only if many unique dst_ports from a single src with low bytes/packet
# has_dos_pattern: true if ppm_max >> ppm_avg by large factor (e.g., >10x)
# has_beaconing: true if iat_cv is very low (highly regular timing)
```

Default to `true` for `is_traffic_benign` and `false` for attack flags unless metrics clearly indicate otherwise. DAPT2020 contains mixed traffic — do not assume malicious by default.

---

## Module 3: Writing the CSV

Write with Python — never use shell heredocs for structured data:

```python
rows = [
    ("protocol_tcp", tcp),
    ("protocol_udp", udp),
    # ... all metrics
]

with open("/root/network_stats.csv", "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["metric", "value"])
    for k, v in rows:
        writer.writerow([k, v])
```

Verify after writing:

```python
import subprocess
result = subprocess.run(["cat", "/root/network_stats.csv"], capture_output=True, text=True)
print(result.stdout)
```

Check: no blank lines, no comment lines, no trailing whitespace, correct row count.

---

## Common Pitfalls

- `protocol_udp` via `p[IP].proto == 17` gives wrong counts — always use `p.haslayer(UDP)`
- `bidirectional_flows` double-counts if you don't divide by 2 after checking both directions
- `iat_variance` should be population variance (`/ n`), not sample variance (`/ (n-1)`)
- Analysis flags default: DAPT2020 traffic is often benign — don't set `has_port_scan=true` or `is_traffic_benign=false` without strong metric evidence
- Shell heredocs corrupt CSV formatting (CRLF issues, escaping) — always write CSV with Python's `csv` module
- Entropy must skip IPs/ports that are `None` — filter before computing
- `network_density` denominator is `n*(n-1)`, not `n^2` — directed graph formula
- `packets_per_minute` buckets are based on elapsed time from first packet, not wall clock
