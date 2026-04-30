---
id: flink-session-windowing-query
title: Flink Session Windowing Query on Cluster Trace Data
category: flink-query
tags: [flink, java, session-windows, gzip-csv, keyed-streams]
difficulty: intermediate
success_rate: high
---

# Flink Session Windowing Query on Cluster Trace Data

## Overview

This skill covers implementing Apache Flink jobs that process Google cluster-usage trace data (gzipped CSV files) to compute session-based aggregations per job. The canonical pattern: parse task/job events, group by job ID, detect inactivity gaps to define stages/sessions, and emit one result per job.

---

## Module 1: Project Structure and Data Model

### Directory Layout

```
src/main/java/clusterdata/
  datatypes/
    TaskEvent.java      # parse task_events CSV
    JobEvent.java       # parse job_events CSV
  query/
    YourQuery.java      # extends AppBase
  utils/
    AppBase.java        # provided — do not modify
```

### Data Schema (Google Cluster Traces)

Task events CSV fields (0-indexed):
```
[0]  timestamp (microseconds)
[1]  missing_info
[2]  job_id
[3]  task_index
[4]  machine_id
[5]  event_type   (0=SUBMIT, 1=SCHEDULE, 2=EVICT, 3=FAIL, 4=FINISH, 5=KILL, 6=LOST, 7=UPDATE_PENDING, 8=UPDATE_RUNNING)
[6]  user
[7]  scheduling_class
[8]  priority
[9]  cpu_req
[10] mem_req
[11] disk_req
[12] diff_machine
```

Job events CSV fields (0-indexed):
```
[0]  timestamp (microseconds)
[1]  missing_info
[2]  job_id
[3]  event_type   (same enum as task events)
[4]  user
[5]  scheduling_class
[6]  job_name
[7]  logical_job_name
```

### Minimal Datatype Classes

```java
// TaskEvent.java
package clusterdata.datatypes;

public class TaskEvent {
    public long timestamp;
    public long jobId;
    public int taskIndex;
    public int eventType;

    public static final int SUBMIT = 0;

    public static TaskEvent fromCsv(String line) {
        String[] f = line.split(",", -1);
        TaskEvent e = new TaskEvent();
        e.timestamp  = f[0].isEmpty() ? 0L : Long.parseLong(f[0]);
        e.jobId      = f[2].isEmpty() ? -1L : Long.parseLong(f[2]);
        e.taskIndex  = f[3].isEmpty() ? -1 : Integer.parseInt(f[3]);
        e.eventType  = f[5].isEmpty() ? -1 : Integer.parseInt(f[5]);
        return e;
    }
}
```

```java
// JobEvent.java
package clusterdata.datatypes;

public class JobEvent {
    public long timestamp;
    public long jobId;
    public int eventType;

    public static final int FINISH = 4;

    public static JobEvent fromCsv(String line) {
        String[] f = line.split(",", -1);
        JobEvent e = new JobEvent();
        e.timestamp = f[0].isEmpty() ? 0L : Long.parseLong(f[0]);
        e.jobId     = f[2].isEmpty() ? -1L : Long.parseLong(f[2]);
        e.eventType = f[3].isEmpty() ? -1 : Integer.parseInt(f[3]);
        return e;
    }
}
```

---

## Module 2: Session Windowing Logic

### Core Algorithm

A "stage" is a maximal group of SUBMIT events where consecutive timestamps (sorted) differ by less than the inactivity threshold. The longest stage is the one with the most events.

```java
// Given a sorted list of SUBMIT timestamps for one job:
static int longestStage(List<Long> timestamps, long gapThreshold) {
    if (timestamps.isEmpty()) return 0;
    Collections.sort(timestamps);
    int maxCount = 1, curCount = 1;
    for (int i = 1; i < timestamps.size(); i++) {
        if (timestamps.get(i) - timestamps.get(i - 1) < gapThreshold) {
            curCount++;
        } else {
            maxCount = Math.max(maxCount, curCount);
            curCount = 1;
        }
    }
    return Math.max(maxCount, curCount);
}
```

Gap threshold for 10 minutes in microseconds: `600_000_000L`

### Reading Gzipped CSV in Flink (File Mode)

When `AppBase.taskEvents == null`, read files directly:

```java
import java.util.zip.GZIPInputStream;
import java.io.*;

List<String> readGzip(String path) throws IOException {
    List<String> lines = new ArrayList<>();
    try (BufferedReader br = new BufferedReader(
            new InputStreamReader(new GZIPInputStream(new FileInputStream(path))))) {
        String line;
        while ((line = br.readLine()) != null) {
            if (!line.trim().isEmpty()) lines.add(line);
        }
    }
    return lines;
}
```

### Full Job Pattern (File Mode)

```java
// Collect SUBMIT timestamps per job
Map<Long, List<Long>> submitsByJob = new HashMap<>();
for (String line : readGzip(taskInputPath)) {
    TaskEvent e = TaskEvent.fromCsv(line);
    if (e.jobId < 0 || e.eventType != TaskEvent.SUBMIT) continue;
    submitsByJob.computeIfAbsent(e.jobId, k -> new ArrayList<>()).add(e.timestamp);
}

// Collect jobs that have a FINISH event
Set<Long> finishedJobs = new HashSet<>();
for (String line : readGzip(jobInputPath)) {
    JobEvent e = JobEvent.fromCsv(line);
    if (e.jobId >= 0 && e.eventType == JobEvent.FINISH) finishedJobs.add(e.jobId);
}

// Emit results
try (PrintWriter pw = new PrintWriter(new FileWriter(outputPath))) {
    for (long jobId : finishedJobs) {
        List<Long> ts = submitsByJob.getOrDefault(jobId, Collections.emptyList());
        int longest = longestStage(ts, 600_000_000L);
        pw.println("(" + jobId + "," + longest + ")");
    }
}
```

### Output Format

One line per job, exactly:
```
(jobId,count)
```
No spaces around the comma. Only emit jobs that have a FINISH event.

---

## Module 3: AppBase Integration and Build

### Checking AppBase Contract

Before writing any code, read `AppBase.java` to understand:
- What fields are exposed (`taskEvents`, `jobEvents`, `output`, etc.)
- Whether test mode injects `DataStream` sources or uses file paths
- What `main()` boilerplate is expected

Typical pattern:
```java
public class YourQuery extends AppBase {
    public static void main(String[] args) throws Exception {
        new YourQuery().run(args);
    }

    @Override
    public void run(String[] args) throws Exception {
        // parse args, then branch on AppBase.taskEvents == null
    }
}
```

### Build Verification

```bash
cd /app/workspace
mvn compile -q                  # check for compile errors first
mvn package -DskipTests -q      # build the jar
```

Always compile before running. Fix all type errors before testing logic.

### Sampling the Data

Before implementing, always sample the actual CSV to confirm field positions:
```bash
zcat data/task_events/part-00001-of-00500.csv.gz | head -5
zcat data/job_events/part-00001-of-00500.csv.gz | head -5
```

Fields can be empty — always guard with `.isEmpty()` checks before parsing.

---

## Common Pitfalls

1. **Wrong field index for event_type**: task events have `event_type` at index 5; job events have it at index 3. Mixing these up silently produces wrong results.

2. **Not filtering by FINISH**: emitting results for all jobs (including running ones) produces extra lines that fail exact-match tests. Only output jobs with a confirmed FINISH event.

3. **Counting resubmits as one**: the spec says "if the same task is submitted then resubmitted, count separately." Don't deduplicate by `(jobId, taskIndex)` — count every SUBMIT event.

4. **Empty field parsing crash**: many fields in the trace are empty strings. Always check `f[i].isEmpty()` before `Long.parseLong(f[i])`.

5. **Gap threshold units**: timestamps are in microseconds. 10 minutes = `600_000_000L` µs, not `600_000L` (milliseconds) or `600L` (seconds).

6. **Split with limit**: use `line.split(",", -1)` not `line.split(",")` — the latter drops trailing empty fields.

7. **Flink streaming vs. batch ordering**: if using two concurrent Flink sources (task + job streams), there's no guaranteed ordering between them. For correctness, either use a `KeyedCoProcessFunction` with state, or read both files sequentially in a single source before processing.

8. **Output file not written**: if the Flink job throws at runtime, the output file may not exist at all. Always wrap file writes in try-with-resources and ensure the output directory exists.
