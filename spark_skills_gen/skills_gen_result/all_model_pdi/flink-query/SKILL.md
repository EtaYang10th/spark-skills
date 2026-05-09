---
title: "Flink Batch-Style Stream Processing on Google Cluster Data"
description: >
  Procedural skill for implementing Apache Flink jobs that read gzipped CSV
  cluster-trace data, apply event-time session windows, join/filter across
  multiple event streams, and produce per-entity aggregations.
  Covers data-type POJOs, custom SourceFunctions for gzipped CSV, watermark
  strategies, session-window counting, cross-stream coordination, and the
  exact output format expected by the test harness.
domain: flink-query
tags:
  - apache-flink
  - google-cluster-data
  - session-windows
  - event-time-processing
  - java
  - stream-processing
version: 1
---

# Flink Batch-Style Stream Processing on Google Cluster Data

## 1. High-Level Workflow

| # | Step | Why |
|---|------|-----|
| 1 | **Read the data-schema docs** (`format.pdf`, `ClusterData2011_2.md`) | Column order, types, and event-type enums differ between task events (13 cols) and job events (8 cols). Getting these wrong silently produces empty output. |
| 2 | **Inspect the skeleton & `AppBase`** | The skeleton wires CLI arg parsing (`ParameterTool`) and may expose helper methods. `AppBase` expects data-type classes in `clusterdata.datatypes`. |
| 3 | **Create POJO data types** (`TaskEvent`, `JobEvent`) | Flink serialization works best with public-field POJOs or Tuples. Each class needs a static `fromCsvLine(String)` factory that tolerates empty/missing fields. |
| 4 | **Implement a `SourceFunction`** that reads gzipped CSV | The input files are `.csv.gz`. Flink's built-in `readTextFile` can handle gzip, but a custom `SourceFunction` gives full control over watermark emission and is more reliable for single-file batch-in-streaming mode. |
| 5 | **Assign event-time timestamps & watermarks** | Timestamps in the dataset are in **microseconds**. Flink watermarks operate in **milliseconds**. Divide by 1000. Use `BoundedOutOfOrdernessTimestampExtractor` or `WatermarkStrategy.forBoundedOutOfOrderness`. |
| 6 | **Filter, key, window, aggregate** | Filter to the relevant event type (e.g., SUBMIT = 0), key by `jobId`, apply `EventTimeSessionWindows.withGap(Time.minutes(10))`, and count elements per window. |
| 7 | **Cross-stream coordination** (e.g., "only finished jobs") | Union the aggregated stream with a marker stream derived from job events. Use `KeyedProcessFunction` with state to emit results only when both the aggregation result and the qualifying marker (e.g., FINISH = 4) are present. |
| 8 | **Write output in the exact tuple format** | The harness expects `(jobId,count)\n` — no spaces after commas, no header, one tuple per line. Use `writeAsText` on a `DataStream<String>` or a custom `SinkFunction`. |
| 9 | **Build with Maven, run on local Flink** | `mvn clean package -DskipTests` → `flink run -t local -c <mainClass> <jar> --arg1 val1 ...` |
| 10 | **Verify output** | `wc -l`, spot-check a few jobIds against `zcat | grep` on the raw data. |

---

## 2. Google Cluster Data Schema

### Task Events — 13 columns

```
0  timestamp (microseconds)
1  missing_info
2  job_id (long)
3  task_index (int)
4  machine_id
5  event_type (int)
6  user
7  scheduling_class
8  priority
9  cpu_request
10 memory_request
11 disk_space_request
12 different_machines_restriction
```

**Task event types:** 0=SUBMIT, 1=SCHEDULE, 2=EVICT, 3=FAIL, 4=FINISH, 5=KILL, 6=LOST, 7=UPDATE_PENDING, 8=UPDATE_RUNNING

### Job Events — 8 columns

```
0  timestamp (microseconds)
1  missing_info
2  job_id (long)
3  event_type (int)
4  user
5  scheduling_class
6  job_name
7  logical_job_name
```

**Job event types:** 0=SUBMIT, 1=SCHEDULE, 2=EVICT, 3=FAIL, 4=FINISH, 5=KILL, 6=LOST, 7=UPDATE_PENDING, 8=UPDATE_RUNNING

### Key rules

- Timestamps are **microseconds since epoch start of the trace** (not Unix epoch).
- Fields can be empty strings — always guard with `field.isEmpty()` before parsing.
- Files are gzipped CSV (`.csv.gz`), no header row.
- A task is uniquely identified by `(job_id, task_index)`, but for counting SUBMIT events, each row is a separate event (resubmissions count separately).

---

## 3. POJO Data Types

### TaskEvent.java

```java
package clusterdata.datatypes;

import java.io.Serializable;

public class TaskEvent implements Serializable {
    public long timestamp;   // microseconds
    public int missingInfo;
    public long jobId;
    public int taskIndex;
    public long machineId;
    public int eventType;
    public String user;
    public int schedulingClass;
    public int priority;
    public double cpuRequest;
    public double memoryRequest;
    public double diskSpaceRequest;
    public int differentMachinesRestriction;

    public TaskEvent() {}

    public static TaskEvent fromCsvLine(String line) {
        String[] f = line.split(",", -1);
        if (f.length < 13) return null;
        try {
            TaskEvent e = new TaskEvent();
            e.timestamp       = f[0].isEmpty()  ? 0L : Long.parseLong(f[0]);
            e.missingInfo     = f[1].isEmpty()  ? 0  : Integer.parseInt(f[1]);
            e.jobId           = f[2].isEmpty()  ? -1L : Long.parseLong(f[2]);
            e.taskIndex       = f[3].isEmpty()  ? 0  : Integer.parseInt(f[3]);
            e.machineId       = f[4].isEmpty()  ? -1L : Long.parseLong(f[4]);
            e.eventType       = f[5].isEmpty()  ? -1 : Integer.parseInt(f[5]);
            e.user            = f[6];
            e.schedulingClass = f[7].isEmpty()  ? 0  : Integer.parseInt(f[7]);
            e.priority        = f[8].isEmpty()  ? 0  : Integer.parseInt(f[8]);
            e.cpuRequest      = f[9].isEmpty()  ? 0.0 : Double.parseDouble(f[9]);
            e.memoryRequest   = f[10].isEmpty() ? 0.0 : Double.parseDouble(f[10]);
            e.diskSpaceRequest= f[11].isEmpty() ? 0.0 : Double.parseDouble(f[11]);
            e.differentMachinesRestriction = f[12].isEmpty() ? 0 : Integer.parseInt(f[12]);
            return e;
        } catch (NumberFormatException ex) {
            return null;
        }
    }
}
```

### JobEvent.java

```java
package clusterdata.datatypes;

import java.io.Serializable;

public class JobEvent implements Serializable {
    public long timestamp;
    public int missingInfo;
    public long jobId;
    public int eventType;
    public String user;
    public int schedulingClass;
    public String jobName;
    public String logicalJobName;

    public JobEvent() {}

    public static JobEvent fromCsvLine(String line) {
        String[] f = line.split(",", -1);
        if (f.length < 8) return null;
        try {
            JobEvent e = new JobEvent();
            e.timestamp       = f[0].isEmpty() ? 0L : Long.parseLong(f[0]);
            e.missingInfo     = f[1].isEmpty() ? 0  : Integer.parseInt(f[1]);
            e.jobId           = f[2].isEmpty() ? -1L : Long.parseLong(f[2]);
            e.eventType       = f[3].isEmpty() ? -1 : Integer.parseInt(f[3]);
            e.user            = f[4];
            e.schedulingClass = f[5].isEmpty() ? 0  : Integer.parseInt(f[5]);
            e.jobName         = f[6];
            e.logicalJobName  = f[7];
            return e;
        } catch (NumberFormatException ex) {
            return null;
        }
    }
}
```

---

## 4. Custom Gzipped-CSV SourceFunction

Flink's `readTextFile` can auto-detect `.gz`, but a custom `SourceFunction` is more explicit and avoids edge cases with parallelism on a single file.

```java
import org.apache.flink.streaming.api.functions.source.SourceFunction;
import java.io.*;
import java.util.zip.GZIPInputStream;

public class GzipCsvSource implements SourceFunction<String> {
    private final String filePath;
    private volatile boolean running = true;

    public GzipCsvSource(String filePath) {
        this.filePath = filePath;
    }

    @Override
    public void run(SourceContext<String> ctx) throws Exception {
        try (BufferedReader br = new BufferedReader(
                new InputStreamReader(
                    new GZIPInputStream(new FileInputStream(filePath))))) {
            String line;
            while (running && (line = br.readLine()) != null) {
                ctx.collect(line);
            }
        }
    }

    @Override
    public void cancel() { running = false; }
}
```

---

## 5. Watermark Strategy — Microseconds to Milliseconds

```java
import org.apache.flink.streaming.api.functions.timestamps.BoundedOutOfOrdernessTimestampExtractor;
import org.apache.flink.streaming.api.windowing.time.Time;

// For TaskEvent stream:
.assignTimestampsAndWatermarks(
    new BoundedOutOfOrdernessTimestampExtractor<TaskEvent>(Time.seconds(0)) {
        @Override
        public long extractTimestamp(TaskEvent e) {
            return e.timestamp / 1000; // microseconds → milliseconds
        }
    }
)
```

Use `Time.seconds(0)` for bounded-out-of-orderness when the data within a single partition file is roughly sorted. If you see missing results, increase to `Time.seconds(1)` or `Time.minutes(1)`.

---

## 6. Session Window + Cross-Stream Join Pattern

The core pattern for "find the longest session per entity, but only for entities that satisfy a condition from another stream":

```
TaskEvents(SUBMIT) ──► keyBy(jobId) ──► SessionWindow(10min) ──► count ──► keyBy(jobId) ──► reduce(max) ──► union ──┐
                                                                                                                     ├──► keyBy(jobId) ──► KeyedProcessFunction ──► output
JobEvents(FINISH)  ──► map to Tuple2<jobId, -1L> (marker) ─────────────────────────────────────────────────────────┘
```

The `KeyedProcessFunction` stores:
- `maxCount`: the maximum session count seen so far for this job
- `isFinished`: whether a FINISH marker arrived

It registers an event-time timer at `Long.MAX_VALUE - 1`. When the timer fires (after all data is processed and the final watermark propagates), it emits the result if `isFinished` is true.

```java
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;

public class EmitFinishedJobs
        extends KeyedProcessFunction<Long, Tuple2<Long, Long>, String> {

    private transient ValueState<Long> maxCount;
    private transient ValueState<Boolean> finished;
    private transient ValueState<Boolean> timerSet;

    @Override
    public void open(Configuration parameters) {
        maxCount  = getRuntimeContext().getState(
            new ValueStateDescriptor<>("maxCount", Long.class));
        finished  = getRuntimeContext().getState(
            new ValueStateDescriptor<>("finished", Boolean.class));
        timerSet  = getRuntimeContext().getState(
            new ValueStateDescriptor<>("timerSet", Boolean.class));
    }

    @Override
    public void processElement(Tuple2<Long, Long> value,
                               Context ctx,
                               Collector<String> out) throws Exception {
        if (value.f1 == -1L) {
            // This is a FINISH marker from the job-events stream
            finished.update(true);
        } else {
            // This is a session count from the task-events stream
            Long cur = maxCount.value();
            if (cur == null || value.f1 > cur) {
                maxCount.update(value.f1);
            }
        }
        // Register a single end-of-time timer per key
        if (timerSet.value() == null) {
            ctx.timerService().registerEventTimeTimer(Long.MAX_VALUE - 1);
            timerSet.update(true);
        }
    }

    @Override
    public void onTimer(long timestamp,
                        OnTimerContext ctx,
                        Collector<String> out) throws Exception {
        Boolean fin = finished.value();
        Long cnt = maxCount.value();
        if (fin != null && fin && cnt != null) {
            out.collect("(" + ctx.getCurrentKey() + "," + cnt + ")");
        }
    }
}
```

---

## 7. Output Format

The test harness compares `(jobId,count)` tuples. Rules:
- One tuple per line
- Format: `(jobId,count)` — parentheses, comma, no spaces
- No header, no trailing blank line (Flink's `writeAsText` is fine)
- Order does not matter
- The harness compares the count for each jobId

```java
// Final stream is DataStream<String> already formatted
resultStream.writeAsText(outputPath,
    org.apache.flink.core.fs.FileSystem.WriteMode.OVERWRITE)
    .setParallelism(1);  // single output file
```

---

## 8. Build & Run

```bash
# Build
cd /app/workspace
mvn clean package -DskipTests

# Run on local Flink
/opt/flink/bin/flink run -t local \
  -c clusterdata.query.LongestSessionPerJob \
  /app/workspace/target/LongestSessionPerJob-jar-with-dependencies.jar \
  --task_input /app/workspace/data/task_events/part-00001-of-00500.csv.gz \
  --job_input /app/workspace/data/job_events/part-00001-of-00500.csv.gz \
  --output /app/workspace/out.txt

# Quick verification
wc -l /app/workspace/out.txt
head -5 /app/workspace/out.txt
```

---

## 9. Common Pitfalls

### Pitfall 1: Timestamp unit mismatch
The dataset uses **microseconds**. Flink watermarks and windows use **milliseconds**. If you forget to divide by 1000, session windows of 10 minutes (600,000 ms) become 0.6 seconds in data-time, and every single SUBMIT event becomes its own session.

### Pitfall 2: Empty CSV fields cause NumberFormatException
Many fields in the CSV are empty strings. Always check `field.isEmpty()` before `Long.parseLong()` / `Integer.parseInt()`. Return a safe default or `null` (and filter nulls downstream).

### Pitfall 3: Wrong column count / column order
Task events have 13 columns, job events have 8. If you use the wrong schema for the wrong file, parsing silently produces garbage. Double-check by `zcat file.gz | head -1 | tr ',' '\n' | wc -l`.

### Pitfall 4: Using processing time instead of event time
The default time characteristic in modern Flink is event time, but you must still assign timestamps and watermarks. Without watermarks, windows never fire.

### Pitfall 5: Parallelism > 1 for output
If the output sink has parallelism > 1, Flink writes multiple part files instead of a single file. The test harness expects a single file. Always `.setParallelism(1)` on the sink.

### Pitfall 6: Forgetting to handle resubmissions
"If the same task is submitted then failed/evicted and resubmitted again, these should be counted separately." Each SUBMIT row is a separate event — do NOT deduplicate by `(jobId, taskIndex)`.

### Pitfall 7: Not filtering to FINISH jobs
The task asks for results "once the job has finished." You must cross-reference with job events where `event_type == 4` (FINISH). Outputting all jobs will fail the test.

### Pitfall 8: Session window gap semantics
`EventTimeSessionWindows.withGap(Time.minutes(10))` means a new window starts when there is a gap of ≥ 10 minutes between consecutive events. Events exactly 10 minutes apart are in different sessions (the gap is exclusive). This matches the task definition of "inactivity period of 10 minutes."

### Pitfall 9: Timer not firing
The `Long.MAX_VALUE - 1` timer fires when the final watermark (`Long.MAX_VALUE`) propagates. This only happens when all sources finish. If a source never completes (e.g., infinite streaming source), the timer never fires. The custom `GzipCsvSource` naturally completes when the file ends, so this works for batch-in-streaming mode.

---

## 10. Reference Implementation

This is the complete, self-contained `LongestSessionPerJob.java`. Copy, adapt, and build.

```java
package clusterdata.query;

import clusterdata.datatypes.JobEvent;
import clusterdata.datatypes.TaskEvent;
import clusterdata.utils.AppBase;

import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.core.fs.FileSystem;
import org.apache.flink.streaming.api.TimeCharacteristic;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.streaming.api.functions.source.SourceFunction;
import org.apache.flink.streaming.api.functions.timestamps.BoundedOutOfOrdernessTimestampExtractor;
import org.apache.flink.streaming.api.functions.windowing.WindowFunction;
import org.apache.flink.streaming.api.windowing.assigners.EventTimeSessionWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.util.Collector;

import java.io.*;
import java.util.zip.GZIPInputStream;

public class LongestSessionPerJob extends AppBase {

    public static void main(String[] args) throws Exception {

        ParameterTool params = ParameterTool.fromArgs(args);
        String taskInput = params.getRequired("task_input");
        String jobInput  = params.getRequired("job_input");
        String output    = params.getRequired("output");

        System.out.println("task_input  " + taskInput);
        System.out.println("job_input  " + jobInput);

        StreamExecutionEnvironment env =
                StreamExecutionEnvironment.getExecutionEnvironment();
        env.setStreamTimeCharacteristic(TimeCharacteristic.EventTime);
        env.setParallelism(1);

        // ── Task-event stream: SUBMIT events only ──────────────────────
        DataStream<TaskEvent> taskEvents = env
            .addSource(new GzipCsvSource(taskInput))
            .map(line -> TaskEvent.fromCsvLine(line))
            .filter(e -> e != null && e.eventType == 0)   // SUBMIT
            .assignTimestampsAndWatermarks(
                new BoundedOutOfOrdernessTimestampExtractor<TaskEvent>(
                        Time.seconds(0)) {
                    @Override
                    public long extractTimestamp(TaskEvent e) {
                        return e.timestamp / 1000;  // μs → ms
                    }
                });

        // Count tasks per session (10-min gap), per job
        DataStream<Tuple2<Long, Long>> sessionCounts = taskEvents
            .keyBy(e -> e.jobId)
            .window(EventTimeSessionWindows.withGap(Time.minutes(10)))
            .apply(new WindowFunction<TaskEvent, Tuple2<Long, Long>,
                                      Long, TimeWindow>() {
                @Override
                public void apply(Long jobId,
                                  TimeWindow window,
                                  Iterable<TaskEvent> input,
                                  Collector<Tuple2<Long, Long>> out) {
                    long count = 0;
                    for (TaskEvent ignored : input) count++;
                    out.collect(new Tuple2<>(jobId, count));
                }
            });

        // Keep only the max session per job
        DataStream<Tuple2<Long, Long>> maxPerJob = sessionCounts
            .keyBy(t -> t.f0)
            .reduce((a, b) -> a.f1 >= b.f1 ? a : b);

        // ── Job-event stream: FINISH markers ───────────────────────────
        DataStream<Tuple2<Long, Long>> finishMarkers = env
            .addSource(new GzipCsvSource(jobInput))
            .map(line -> JobEvent.fromCsvLine(line))
            .filter(e -> e != null && e.eventType == 4)   // FINISH
            .assignTimestampsAndWatermarks(
                new BoundedOutOfOrdernessTimestampExtractor<JobEvent>(
                        Time.seconds(0)) {
                    @Override
                    public long extractTimestamp(JobEvent e) {
                        return e.timestamp / 1000;
                    }
                })
            .map(e -> new Tuple2<>(e.jobId, -1L))
            .returns(org.apache.flink.api.common.typeinfo.Types
                .TUPLE(org.apache.flink.api.common.typeinfo.Types.LONG,
                       org.apache.flink.api.common.typeinfo.Types.LONG));

        // ── Union & emit only finished jobs ────────────────────────────
        DataStream<String> result = maxPerJob
            .union(finishMarkers)
            .keyBy(t -> t.f0)
            .process(new KeyedProcessFunction<Long,
                            Tuple2<Long, Long>, String>() {

                private transient ValueState<Long> maxCount;
                private transient ValueState<Boolean> finished;
                private transient ValueState<Boolean> timerSet;

                @Override
                public void open(Configuration cfg) {
                    maxCount = getRuntimeContext().getState(
                        new ValueStateDescriptor<>("mc", Long.class));
                    finished = getRuntimeContext().getState(
                        new ValueStateDescriptor<>("fin", Boolean.class));
                    timerSet = getRuntimeContext().getState(
                        new ValueStateDescriptor<>("ts", Boolean.class));
                }

                @Override
                public void processElement(
                        Tuple2<Long, Long> val,
                        Context ctx,
                        Collector<String> out) throws Exception {
                    if (val.f1 == -1L) {
                        finished.update(true);
                    } else {
                        Long cur = maxCount.value();
                        if (cur == null || val.f1 > cur) {
                            maxCount.update(val.f1);
                        }
                    }
                    if (timerSet.value() == null) {
                        ctx.timerService()
                           .registerEventTimeTimer(Long.MAX_VALUE - 1);
                        timerSet.update(true);
                    }
                }

                @Override
                public void onTimer(long ts,
                                    OnTimerContext ctx,
                                    Collector<String> out) throws Exception {
                    Boolean fin = finished.value();
                    Long cnt = maxCount.value();
                    if (fin != null && fin && cnt != null) {
                        out.collect("(" + ctx.getCurrentKey()
                                    + "," + cnt + ")");
                    }
                }
            });

        result.writeAsText(output, FileSystem.WriteMode.OVERWRITE)
              .setParallelism(1);

        env.execute("LongestSessionPerJob");
    }

    // ── Gzipped CSV source ─────────────────────────────────────────────
    public static class GzipCsvSource implements SourceFunction<String> {
        private final String path;
        private volatile boolean running = true;

        public GzipCsvSource(String path) { this.path = path; }

        @Override
        public void run(SourceContext<String> ctx) throws Exception {
            try (BufferedReader br = new BufferedReader(
                    new InputStreamReader(
                        new GZIPInputStream(
                            new FileInputStream(path))))) {
                String line;
                while (running && (line = br.readLine()) != null) {
                    ctx.collect(line);
                }
            }
        }

        @Override
        public void cancel() { running = false; }
    }
}
```

### Supporting files needed alongside the reference implementation

**`clusterdata/datatypes/TaskEvent.java`** — see Section 3 above (full code provided).

**`clusterdata/datatypes/JobEvent.java`** — see Section 3 above (full code provided).

**`pom.xml`** — do not modify; it defines the main class and the `jar-with-dependencies` build.

### Build & run commands

```bash
cd /app/workspace
mvn clean package -DskipTests

/opt/flink/bin/flink run -t local \
  -c clusterdata.query.LongestSessionPerJob \
  target/LongestSessionPerJob-jar-with-dependencies.jar \
  --task_input /app/workspace/data/task_events/part-00001-of-00500.csv.gz \
  --job_input  /app/workspace/data/job_events/part-00001-of-00500.csv.gz \
  --output     /app/workspace/out.txt
```

### Verification checklist

```bash
# 1. File exists and has content
wc -l /app/workspace/out.txt

# 2. Format is correct
head -5 /app/workspace/out.txt
# Expected: (12345,7)  — parentheses, no spaces

# 3. Spot-check a specific job
JOB=6253404208          # pick from output
zcat data/task_events/part-00001-of-00500.csv.gz \
  | awk -F, '$3=="'$JOB'" && $6=="0"' | wc -l
# This gives total SUBMIT count; the output count should be ≤ this
# (it's the max session, not total)

# 4. Confirm only finished jobs appear
zcat data/job_events/part-00001-of-00500.csv.gz \
  | awk -F, '$4=="4" {print $3}' | sort -u > /tmp/finished_jobs.txt
awk -F'[(),]' '{print $2}' /app/workspace/out.txt | sort -u > /tmp/output_jobs.txt
comm -23 /tmp/output_jobs.txt /tmp/finished_jobs.txt
# Should produce no output (all output jobs are finished)
```

---

## 11. Adaptation Guide for Similar Tasks

| Variation | What to change |
|-----------|---------------|
| Different event-type filter | Change the `filter(e -> e.eventType == X)` predicate |
| Different window type (tumbling, sliding) | Replace `EventTimeSessionWindows` with `TumblingEventTimeWindows` or `SlidingEventTimeWindows` |
| Different gap duration | Change `Time.minutes(10)` to the required gap |
| Aggregate differently (sum, avg, min) | Change the `WindowFunction` body and the downstream `reduce` |
| No cross-stream filter | Remove the job-events stream, the union, and the `KeyedProcessFunction`; write session counts directly |
| Multiple output fields | Change the `onTimer` output format to match the required tuple schema |
| Different dataset columns | Adjust the POJO field count and `fromCsvLine` parsing indices |