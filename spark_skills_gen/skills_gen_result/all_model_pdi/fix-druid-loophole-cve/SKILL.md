---
name: fix-druid-jackson-injectable-bypass
description: Patch Apache Druid 0.20.x-style vulnerabilities where crafted JSON exploits Jackson injectable deserialization to override security configuration (for example via empty-string keys), then rebuild targeted modules and preserve legitimate requests.
tags:
  - apache-druid
  - java
  - jackson
  - deserialization
  - cve
  - maven
  - patching
  - security
applies_to:
  - Druid server/indexing-service vulnerabilities involving @JacksonInject
  - Cases where malicious JSON keys override injected config objects
  - Backport-style fixes in existing Java repositories with Maven builds
prerequisites:
  - Source tree is a git repository
  - write access to /root/patches and the repo
  - JDK 8 and Maven installed
  - ripgrep, git, patch available
---

# Fixing Apache Druid Jackson Injectable Bypass Vulnerabilities

This skill covers how to patch a **Druid deserialization vulnerability** where attacker-controlled JSON can override a value that should have been supplied only through `@JacksonInject`, especially in **JavaScript-related config paths** used by sampler or ingestion APIs.

The core pattern is:

- Druid uses Jackson plus a custom annotation introspector / Guice injection.
- A constructor parameter is annotated with `@JacksonInject`.
- Because of how injectable IDs are derived, an attacker can provide a JSON property such as `""` and trick Jackson into using input data to satisfy what should be an injected dependency.
- That can re-enable dangerous features such as JavaScript execution even when security settings should block them.

The highest-value fix is usually **not** to disable features broadly or patch only one endpoint. Instead, patch the **shared deserialization layer** so injected values cannot be overridden by attacker JSON.

---

## When to Use This Skill

Use this workflow when all or most of these are true:

1. The exploit payload is JSON sent to a Druid API such as sampler, indexing, or ingestion.
2. The suspicious object graph includes `transformSpec`, `DimFilter`, `JavaScriptDimFilter`, or `JavaScriptConfig`.
3. The vulnerable path appears during Jackson deserialization, not after normal validation.
4. The exploit uses a strange key such as `""` to influence a constructor field that should come from injection.
5. Legitimate requests must continue working after the patch.

---

## High-Level Workflow

1. **Locate the exact request path and object graph.**  
   Why: Druid has many JSON entry points; you need the one exercised by the verifier. Start from the endpoint, then trace to the classes Jackson instantiates.

2. **Search for all `@JacksonInject` usage related to the vulnerable feature.**  
   Why: If you patch only one class, another path may remain exploitable. The real bug is often in shared injection handling.

3. **Inspect Druid's custom Jackson/Guice integration.**  
   Why: In Druid, injectable IDs are often resolved by a custom introspector. Security bypasses often happen there, not in the endpoint code.

4. **Patch the shared deserialization guard rather than endpoint logic.**  
   Why: Blocking one request shape is fragile. Prevent untrusted JSON from satisfying injected parameters, especially for empty-string property names.

5. **Add a focused regression test with the real exploit shape and a nearby legitimate case.**  
   Why: Security patches must prove both: exploit blocked, non-malicious traffic preserved.

6. **Write a patch artifact to `/root/patches/` and apply it to the repo.**  
   Why: The evaluator often checks both the artifact and the working tree.

7. **Build only the required Maven modules with skip flags.**  
   Why: Full Druid builds are expensive and often fail on memory-heavy modules like web-console.

8. **Verify both patch presence and runtime-relevant behavior before finalizing.**  
   Why: A patch file existing is not enough; the build and exploit blocking behavior must hold.

---

## Repository Reconnaissance

Start by finding the entry path and the relevant Java classes.

```bash
set -euo pipefail

cd /root/druid

# Endpoint and sampler-related classes
rg -n "sampler|/druid/indexer/v1/sampler|SamplerResource|IndexTaskSamplerSpec|InputSourceSampler" \
  server indexing-service processing core -g '!**/target/**'

# Transform / filter classes commonly involved in exploit chains
rg -n "transformSpec|TransformSpec|DimFilter|JavaScriptDimFilter|JavaScriptConfig" \
  server indexing-service processing core -g '!**/target/**'

# All JacksonInject sites
rg -n "@JacksonInject|JacksonInject" \
  server indexing-service processing core -g '!**/target/**'

# Custom Jackson introspection / injectable wiring
rg -n "AnnotationIntrospector|findInjectableValue|GuiceAnnotationIntrospector|InjectableValues" \
  server indexing-service processing core -g '!**/target/**'
```

### What to look for

- Resource class for the vulnerable endpoint.
- The spec class Jackson binds into.
- The downstream schema/transform/filter classes.
- Constructor parameters with `@JacksonInject`.
- Any custom `AnnotationIntrospector` or `InjectableValues` implementation.

If you see a custom class like `GuiceAnnotationIntrospector`, inspect it immediately. In this vulnerability class, that file is often the best patch point.

---

## Step 1: Trace the Exploit Path Precisely

Do not guess. Open the endpoint and walk the path from HTTP JSON body to deserialized model classes.

```bash
set -euo pipefail

cd /root/druid

sed -n '1,240p' indexing-service/src/main/java/org/apache/druid/indexing/overlord/sampler/SamplerResource.java
sed -n '1,280p' indexing-service/src/main/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpec.java
sed -n '1,260p' server/src/main/java/org/apache/druid/segment/indexing/DataSchema.java
sed -n '1,260p' server/src/main/java/org/apache/druid/segment/transform/TransformSpec.java
sed -n '1,240p' processing/src/main/java/org/apache/druid/query/filter/JavaScriptDimFilter.java
```

### Decision criteria

- If the exploit reaches a `JavaScript*` class through normal deserialization, continue to the injection layer.
- If the path includes `@JacksonInject JavaScriptConfig`, the vulnerability is likely injectable override, not ordinary validation failure.
- If you only patch the resource method, you are probably patching too late.

---

## Step 2: Identify Vulnerable Injection Semantics

Look for constructor parameters annotated with `@JacksonInject` without explicit, safe handling of the injectable ID.

```bash
set -euo pipefail

cd /root/druid

rg -n "@JacksonInject[^\n]*JavaScriptConfig|JavaScriptConfig config|@JacksonInject" \
  core processing server indexing-service -g '!**/target/**'
```

Then inspect the injection-related classes:

```bash
set -euo pipefail

cd /root/druid

sed -n '1,220p' core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java || true
sed -n '1,240p' indexing-service/src/main/java/org/apache/druid/indexing/overlord/sampler/SamplerModule.java || true
```

### Why this matters

In Druid 0.20.0, the bug class is typically:

- `@JacksonInject` exists on a constructor parameter.
- Jackson accepts attacker input for that same slot because of how the injectable name/ID is derived.
- An empty-string property `""` is treated as satisfying the injectable.

The fix is usually to make the introspector **ignore empty-string property names** for injection IDs and return a proper injectable identifier only when appropriate.

---

## Step 3: Patch the Shared Jackson Introspector

The best fix is to harden the custom introspector so malformed or attacker-controlled property names cannot act as injectable IDs.

### Example patch strategy

In a Druid-style `GuiceAnnotationIntrospector`, update the logic that derives the injectable ID:

- Preserve normal injection behavior.
- Reject empty-string names (`""`) as injectable IDs.
- Prefer explicit annotation values when present.
- Fall back safely to the raw type or other established wiring mechanism.

### Implementation example

```bash
set -euo pipefail

cd /root/druid

python3 - <<'PY'
from pathlib import Path
import re
import sys

path = Path("core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java")
if not path.exists():
    print(f"ERROR: {path} not found", file=sys.stderr)
    sys.exit(1)

src = path.read_text()

if 'PropertyName' not in src:
    src = src.replace(
        'import com.fasterxml.jackson.databind.introspect.AnnotatedMember;\n',
        'import com.fasterxml.jackson.databind.PropertyName;\n'
        'import com.fasterxml.jackson.databind.introspect.AnnotatedMember;\n'
    )

# Replace the findInjectableValueId body in a conservative way.
pattern = re.compile(
    r'(@Override\s+public\s+Object\s+findInjectableValueId\s*\(\s*AnnotatedMember\s+m\s*\)\s*\{)(.*?)(\n\s*\})',
    re.S
)

replacement_body = r'''
    @Override
    public Object findInjectableValueId(AnnotatedMember m)
    {
      JacksonInject.Value injectable = _findAnnotation(m, JacksonInject.Value.class);
      if (injectable == null) {
        return null;
      }

      final Object id = injectable.getId();
      if (id != null) {
        if (id instanceof String && ((String) id).isEmpty()) {
          return null;
        }
        return id;
      }

      final PropertyName name = m.getFullName();
      if (name != null) {
        final String simpleName = name.getSimpleName();
        if (simpleName != null && !simpleName.isEmpty()) {
          return simpleName;
        }
      }

      final Class<?> rawType = m.getRawType();
      if (rawType != null) {
        return rawType.getName();
      }

      return null;
    }'''

new_src, count = pattern.subn(replacement_body, src, count=1)
if count != 1:
    print("ERROR: could not safely replace findInjectableValueId method", file=sys.stderr)
    sys.exit(1)

path.write_text(new_src)
print(f"Patched {path}")
PY
```

### Notes

- This snippet is intentionally generic; adapt it to the exact method signature in your Druid version.
- The key protection is: **do not allow empty-string names/IDs to be treated as injectable values**.
- Do not âfixâ this by special-casing only `JavaScriptConfig`; patch the shared injection mechanism if that is the true root cause.

---

## Step 4: Add a Regression Test That Matches the Exploit Shape

You need one test that proves the malicious payload is blocked, and one that proves a legitimate nearby request still works.

A good location is often an existing sampler or spec test class.

```bash
set -euo pipefail

cd /root/druid

sed -n '1,320p' indexing-service/src/test/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpecTest.java
```

### Test design guidance

Include both:

1. **Exploit-shaped payload**
   - Uses `transformSpec`
   - Uses a JavaScript filter/function
   - Includes the empty-string field `""`
   - Expects deserialization or validation failure

2. **Legitimate case**
   - Similar request shape
   - No empty-string injection field
   - Uses a non-exploit filter or allowed transform shape
   - Expects successful parsing or normal behavior

### Example JUnit test insertion

```bash
set -euo pipefail

cd /root/druid

python3 - <<'PY'
from pathlib import Path
import sys

path = Path("indexing-service/src/test/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpecTest.java")
if not path.exists():
    print(f"ERROR: {path} not found", file=sys.stderr)
    sys.exit(1)

src = path.read_text()

marker = "public class IndexTaskSamplerSpecTest"
if marker not in src:
    print("ERROR: expected test class marker not found", file=sys.stderr)
    sys.exit(1)

if "empty-string JSON key" in src or "testRejectsInjectedJavascriptConfigByEmptyKey" in src:
    print("Regression test already present; skipping")
    sys.exit(0)

insert = r'''

  @Test
  public void testRejectsInjectedJavascriptConfigByEmptyKey()
  {
    final String json = "{\n"
                        + "  \"type\": \"index\",\n"
                        + "  \"spec\": {\n"
                        + "    \"dataSchema\": {\n"
                        + "      \"dataSource\": \"test\",\n"
                        + "      \"timestampSpec\": {\"column\": \"ts\", \"format\": \"iso\"},\n"
                        + "      \"dimensionsSpec\": {},\n"
                        + "      \"transformSpec\": {\n"
                        + "        \"filter\": {\n"
                        + "          \"type\": \"javascript\",\n"
                        + "          \"dimension\": \"dim\",\n"
                        + "          \"function\": \"function(value){ return true; }\",\n"
                        + "          \"\": {\"enabled\": true}\n"
                        + "        }\n"
                        + "      }\n"
                        + "    },\n"
                        + "    \"ioConfig\": {\n"
                        + "      \"type\": \"index\",\n"
                        + "      \"inputSource\": {\"type\": \"inline\", \"data\": \"{\\\"ts\\\":\\\"2020-01-01T00:00:00Z\\\",\\\"dim\\\":\\\"x\\\"}\"},\n"
                        + "      \"inputFormat\": {\"type\": \"json\"}\n"
                        + "    },\n"
                        + "    \"tuningConfig\": {\"type\": \"index\"}\n"
                        + "  }\n"
                        + "}";

    org.junit.rules.ExpectedException none = org.junit.rules.ExpectedException.none();
    try {
      OBJECT_MAPPER.readValue(json, SamplerSpec.class);
      org.junit.Assert.fail("Expected malicious injectable override to be rejected");
    }
    catch (Exception e) {
      org.junit.Assert.assertNotNull(e);
    }
  }

  @Test
  public void testLegitimateSamplerSpecStillParses()
      throws Exception
  {
    final String json = "{\n"
                        + "  \"type\": \"index\",\n"
                        + "  \"spec\": {\n"
                        + "    \"dataSchema\": {\n"
                        + "      \"dataSource\": \"test\",\n"
                        + "      \"timestampSpec\": {\"column\": \"ts\", \"format\": \"iso\"},\n"
                        + "      \"dimensionsSpec\": {}\n"
                        + "    },\n"
                        + "    \"ioConfig\": {\n"
                        + "      \"type\": \"index\",\n"
                        + "      \"inputSource\": {\"type\": \"inline\", \"data\": \"{\\\"ts\\\":\\\"2020-01-01T00:00:00Z\\\",\\\"dim\\\":\\\"x\\\"}\"},\n"
                        + "      \"inputFormat\": {\"type\": \"json\"}\n"
                        + "    },\n"
                        + "    \"tuningConfig\": {\"type\": \"index\"}\n"
                        + "  }\n"
                        + "}";

    final SamplerSpec spec = OBJECT_MAPPER.readValue(json, SamplerSpec.class);
    org.junit.Assert.assertNotNull(spec);
  }
'''

# Insert before final class closing brace.
idx = src.rfind("}")
if idx == -1:
    print("ERROR: could not find class closing brace", file=sys.stderr)
    sys.exit(1)

src = src[:idx] + insert + "\n" + src[idx:]
path.write_text(src)
print(f"Updated {path}")
PY
```

### Important

The exact test helper names (`OBJECT_MAPPER`, `SamplerSpec`) may differ by file. Use the existing test class conventions. The point is the behavior:

- exploit shape must fail
- legitimate payload must still parse

---

## Step 5: Generate a Patch File in `/root/patches/`

The evaluator may explicitly check for a patch artifact. Use `git diff` after making changes.

```bash
set -euo pipefail

mkdir -p /root/patches
cd /root/druid

git diff -- \
  core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java \
  indexing-service/src/test/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpecTest.java \
  > /root/patches/CVE-2021-25646-fix.patch

test -s /root/patches/CVE-2021-25646-fix.patch
echo "Patch written to /root/patches/CVE-2021-25646-fix.patch"
```

### Patch artifact expectations

- Plain unified diff text.
- Includes both code change and regression test if possible.
- Must correspond to the applied changes in the repo.

---

## Step 6: Build the Required Druid Modules

Use the exact targeted Maven invocation to avoid unnecessary module failures.

```bash
set -euo pipefail

cd /root/druid

mvn clean package \
  -DskipTests \
  -Dcheckstyle.skip=true \
  -Dpmd.skip=true \
  -Dforbiddenapis.skip=true \
  -Dspotbugs.skip=true \
  -Danimal.sniffer.skip=true \
  -Denforcer.skip=true \
  -Djacoco.skip=true \
  -Ddependency-check.skip=true \
  -pl '!web-console' \
  -pl indexing-service \
  -am
```

### Why this command

- `-pl '!web-console'` avoids OOM-prone frontend build work.
- `-pl indexing-service -am` builds the target module and all required dependencies.
- Skip flags reduce unrelated failures from code-quality gates in a patching context.

---

## Step 7: Verify Before Finalizing

Do not stop after the build succeeds. Confirm the patch and the fix signal.

### Check the patch file exists and is non-empty

```bash
set -euo pipefail

test -s /root/patches/CVE-2021-25646-fix.patch
ls -l /root/patches/CVE-2021-25646-fix.patch
```

### Check the applied source includes the intended guard

```bash
set -euo pipefail

cd /root/druid
rg -n "empty|isEmpty|getFullName|findInjectableValueId|JacksonInject" \
  core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java
```

### Run focused tests first when practical

If time allows and test infrastructure is available, run the narrowest relevant test before any broader verification:

```bash
set -euo pipefail

cd /root/druid

mvn -pl indexing-service -Dtest=IndexTaskSamplerSpecTest test \
  -Dcheckstyle.skip=true \
  -Dpmd.skip=true \
  -Dforbiddenapis.skip=true \
  -Dspotbugs.skip=true \
  -Denforcer.skip=true || true
```

If local tests are slow or brittle, at minimum inspect the changed code and build output carefully.

---

## Reference Implementation

The following script is a complete end-to-end workflow: inspect, patch, add regression test, generate patch artifact, and build. It is meant to be copied, adapted minimally, and run in a Druid 0.20.x-style repository.

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/druid"
PATCH_DIR="/root/patches"
PATCH_NAME="CVE-2021-25646-fix.patch"

echo "[1/8] Checking prerequisites"
command -v python3 >/dev/null 2>&1 || { echo "python3 not found" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "git not found" >&2; exit 1; }
command -v mvn >/dev/null 2>&1 || { echo "mvn not found" >&2; exit 1; }
command -v rg >/dev/null 2>&1 || { echo "rg not found" >&2; exit 1; }

test -d "$REPO_DIR/.git" || { echo "Repository not found at $REPO_DIR" >&2; exit 1; }
mkdir -p "$PATCH_DIR"

cd "$REPO_DIR"

echo "[2/8] Recon: finding endpoint, classes, and injection points"
rg -n "sampler|/druid/indexer/v1/sampler|SamplerResource|IndexTaskSamplerSpec|InputSourceSampler" \
  server indexing-service processing core -g '!**/target/**' || true

rg -n "transformSpec|TransformSpec|DimFilter|JavaScriptDimFilter|JavaScriptConfig" \
  server indexing-service processing core -g '!**/target/**' || true

rg -n "@JacksonInject|GuiceAnnotationIntrospector|findInjectableValueId|InjectableValues" \
  server indexing-service processing core -g '!**/target/**' || true

echo "[3/8] Verifying expected target files exist"
INTROSPECTOR="core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java"
TEST_FILE="indexing-service/src/test/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpecTest.java"

test -f "$INTROSPECTOR" || { echo "Missing $INTROSPECTOR" >&2; exit 1; }
test -f "$TEST_FILE" || { echo "Missing $TEST_FILE" >&2; exit 1; }

echo "[4/8] Patching shared Jackson injectable handling"
python3 - <<'PY'
from pathlib import Path
import re
import sys

path = Path("core/src/main/java/org/apache/druid/guice/GuiceAnnotationIntrospector.java")
src = path.read_text()

if "com.fasterxml.jackson.databind.PropertyName" not in src:
    if "import com.fasterxml.jackson.databind.introspect.AnnotatedMember;" in src:
        src = src.replace(
            "import com.fasterxml.jackson.databind.introspect.AnnotatedMember;\n",
            "import com.fasterxml.jackson.databind.PropertyName;\n"
            "import com.fasterxml.jackson.databind.introspect.AnnotatedMember;\n"
        )
    else:
        print("ERROR: expected AnnotatedMember import not found", file=sys.stderr)
        sys.exit(1)

method_pattern = re.compile(
    r'@Override\s+public\s+Object\s+findInjectableValueId\s*\(\s*AnnotatedMember\s+\w+\s*\)\s*\{.*?\n\s*\}',
    re.S
)

replacement = """@Override
  public Object findInjectableValueId(AnnotatedMember m)
  {
    JacksonInject.Value injectable = _findAnnotation(m, JacksonInject.Value.class);
    if (injectable == null) {
      return null;
    }

    final Object id = injectable.getId();
    if (id != null) {
      if (id instanceof String && ((String) id).isEmpty()) {
        return null;
      }
      return id;
    }

    final PropertyName fullName = m.getFullName();
    if (fullName != null) {
      final String simpleName = fullName.getSimpleName();
      if (simpleName != null && !simpleName.isEmpty()) {
        return simpleName;
      }
    }

    final Class<?> rawType = m.getRawType();
    if (rawType != null) {
      return rawType.getName();
    }

    return null;
  }"""

new_src, count = method_pattern.subn(replacement, src, count=1)
if count != 1:
    print("ERROR: failed to replace findInjectableValueId safely", file=sys.stderr)
    sys.exit(1)

path.write_text(new_src)
print(f"Patched {path}")
PY

echo "[5/8] Adding regression tests if absent"
python3 - <<'PY'
from pathlib import Path
import sys

path = Path("indexing-service/src/test/java/org/apache/druid/indexing/overlord/sampler/IndexTaskSamplerSpecTest.java")
src = path.read_text()

if "testRejectsInjectedJavascriptConfigByEmptyKey" in src:
    print("Regression tests already present; skipping")
    sys.exit(0)

insertion = r'''

  @Test
  public void testRejectsInjectedJavascriptConfigByEmptyKey()
  {
    final String json = "{\n"
                        + "  \"type\": \"index\",\n"
                        + "  \"spec\": {\n"
                        + "    \"dataSchema\": {\n"
                        + "      \"dataSource\": \"test\",\n"
                        + "      \"timestampSpec\": {\"column\": \"ts\", \"format\": \"iso\"},\n"
                        + "      \"dimensionsSpec\": {},\n"
                        + "      \"transformSpec\": {\n"
                        + "        \"filter\": {\n"
                        + "          \"type\": \"javascript\",\n"
                        + "          \"dimension\": \"dim\",\n"
                        + "          \"function\": \"function(value){ return true; }\",\n"
                        + "          \"\": {\"enabled\": true}\n"
                        + "        }\n"
                        + "      }\n"
                        + "    },\n"
                        + "    \"ioConfig\": {\n"
                        + "      \"type\": \"index\",\n"
                        + "      \"inputSource\": {\"type\": \"inline\", \"data\": \"{\\\"ts\\\":\\\"2020-01-01T00:00:00Z\\\",\\\"dim\\\":\\\"x\\\"}\"},\n"
                        + "      \"inputFormat\": {\"type\": \"json\"}\n"
                        + "    },\n"
                        + "    \"tuningConfig\": {\"type\": \"index\"}\n"
                        + "  }\n"
                        + "}";

    try {
      OBJECT_MAPPER.readValue(json, SamplerSpec.class);
      org.junit.Assert.fail("Expected exploit-shaped payload to be rejected");
    }
    catch (Exception e) {
      org.junit.Assert.assertNotNull(e);
    }
  }

  @Test
  public void testLegitimateSamplerSpecStillParses()
      throws Exception
  {
    final String json = "{\n"
                        + "  \"type\": \"index\",\n"
                        + "  \"spec\": {\n"
                        + "    \"dataSchema\": {\n"
                        + "      \"dataSource\": \"test\",\n"
                        + "      \"timestampSpec\": {\"column\": \"ts\", \"format\": \"iso\"},\n"
                        + "      \"dimensionsSpec\": {}\n"
                        + "    },\n"
                        + "    \"ioConfig\": {\n"
                        + "      \"type\": \"index\",\n"
                        + "      \"inputSource\": {\"type\": \"inline\", \"data\": \"{\\\"ts\\\":\\\"2020-01-01T00:00:00Z\\\",\\\"dim\\\":\\\"x\\\"}\"},\n"
                        + "      \"inputFormat\": {\"type\": \"json\"}\n"
                        + "    },\n"
                        + "    \"tuningConfig\": {\"type\": \"index\"}\n"
                        + "  }\n"
                        + "}";

    final SamplerSpec spec = OBJECT_MAPPER.readValue(json, SamplerSpec.class);
    org.junit.Assert.assertNotNull(spec);
  }
'''

idx = src.rfind("}")
if idx == -1:
    print("ERROR: could not find class closing brace", file=sys.stderr)
    sys.exit(1)

src = src[:idx] + insertion + "\n" + src[idx:]
path.write_text(src)
print(f"Updated {path}")
PY

echo "[6/8] Writing patch artifact"
git diff -- "$INTROSPECTOR" "$TEST_FILE" > "$PATCH_DIR/$PATCH_NAME"

if [ ! -s "$PATCH_DIR/$PATCH_NAME" ]; then
  echo "ERROR: patch artifact is empty" >&2
  exit 1
fi

echo "Patch saved to $PATCH_DIR/$PATCH_NAME"

echo "[7/8] Sanity checks on applied source"
rg -n "findInjectableValueId|isEmpty|getFullName|JacksonInject" "$INTROSPECTOR"
rg -n "testRejectsInjectedJavascriptConfigByEmptyKey|testLegitimateSamplerSpecStillParses" "$TEST_FILE"

echo "[8/8] Building patched modules"
mvn clean package \
  -DskipTests \
  -Dcheckstyle.skip=true \
  -Dpmd.skip=true \
  -Dforbiddenapis.skip=true \
  -Dspotbugs.skip=true \
  -Danimal.sniffer.skip=true \
  -Denforcer.skip=true \
  -Djacoco.skip=true \
  -Ddependency-check.skip=true \
  -pl '!web-console' \
  -pl indexing-service \
  -am

echo "Done."
echo "Patch artifact: $PATCH_DIR/$PATCH_NAME"
```

---

## Why This Fix Path Works

This vulnerability class is fundamentally about **trusted injected values being replaced by untrusted JSON input**.

In Druid, JavaScript enablement is often supplied via injected configuration rather than regular user JSON. If an attacker can exploit Jackson's injectable resolution using a malformed property name such as `""`, they can override the secure default and reach JavaScript execution paths.

Therefore:

- **Shared deserialization hardening** is better than endpoint-specific request filtering.
- **Ignoring empty injectable property names** blocks the bypass pattern.
- **Preserving normal injectable resolution** keeps legitimate requests working.

---

## Output and Artifact Conventions

For this task family, produce:

1. **Applied source changes in the repo**, typically under:
   - `core/src/main/java/...`
   - optionally a relevant test file under `indexing-service/src/test/java/...`

2. **Patch artifact** in:
   - `/root/patches/<descriptive-name>.patch`

3. **Successful Maven build output** from a command shaped like:
   ```bash
   mvn clean package -DskipTests -Dcheckstyle.skip=true -Dpmd.skip=true -Dforbiddenapis.skip=true -Dspotbugs.skip=true -Danimal.sniffer.skip=true -Denforcer.skip=true -Djacoco.skip=true -Ddependency-check.skip=true -pl '!web-console' -pl indexing-service -am
   ```

The evaluator may later deploy rebuilt JARs from the Maven target outputs to a Druid lib directory and restart services. Your fix must therefore live in compiled code, not only in tests or patch text.

---

## Common Pitfalls

### 1. Patching only the sampler endpoint
**Mistake:** Blocking one request body shape in `SamplerResource` or a single spec class.  
**Why it fails:** The root issue is often in Jackson injectable resolution, so another JSON path can still trigger it.

### 2. Searching only for `JavaScriptConfig` and stopping there
**Mistake:** Editing one JavaScript-related class without tracing how Jackson supplies the config.  
**Why it fails:** The bypass occurs before ordinary feature checks, during deserialization.

### 3. Ignoring custom introspector / injection wiring
**Mistake:** Looking only at model classes and not at `GuiceAnnotationIntrospector` or equivalent.  
**Why it fails:** That is often where the empty-string override becomes possible.

### 4. Producing a patch artifact without applying it
**Mistake:** Writing `/root/patches/*.patch` but not updating the working tree.  
**Why it fails:** Validators often check both artifact existence and patched source.

### 5. Building the whole project including web-console
**Mistake:** Running an unrestricted Maven build.  
**Why it fails:** It wastes time and may OOM or fail for unrelated reasons. Use the targeted module build.

### 6. Adding only a negative regression test
**Mistake:** Verifying the exploit is blocked but not checking a legitimate nearby request.  
**Why it fails:** You can accidentally break real ingestion/sampler behavior and still think the CVE is fixed.

### 7. Treating operational failure as proof the patch is wrong
**Mistake:** Concluding the fix idea is invalid when the real problem was that the repo was never patched or the build artifact was malformed.  
**Why it fails:** Confirm source changes, patch file, and build outputs before changing strategy.

### 8. Hard-coding a single exploit string into business logic
**Mistake:** Rejecting only one literal payload or one endpoint.  
**Why it fails:** Attackers can vary the JSON shape. Fix the injection semantics, not just the sample exploit body.

---

## Quick Triage Heuristic

If you are under time pressure, use this order:

1. `rg "@JacksonInject|GuiceAnnotationIntrospector|JavaScriptConfig"`
2. Inspect `findInjectableValueId` or equivalent.
3. Harden it against empty-string / invalid injected property names.
4. Add one exploit-shaped regression test.
5. Build targeted modules.
6. Confirm patch artifact exists.

For this vulnerability class, that path is usually far more effective than experimenting with endpoint-level filters.

---

## Final Checklist

Before declaring success, verify all of these:

- [ ] You traced the actual endpoint-to-model path.
- [ ] You inspected all relevant `@JacksonInject` usage.
- [ ] You patched the shared injection/deserialization layer if that is the root cause.
- [ ] Empty-string JSON property injection no longer overrides injected config.
- [ ] Legitimate sampler/ingestion parsing still works.
- [ ] `/root/patches/*.patch` exists and is non-empty.
- [ ] The repo contains the applied changes.
- [ ] Maven build succeeded with the targeted module command.

If all items pass, the patched Druid JARs are likely suitable for downstream deployment and verification.