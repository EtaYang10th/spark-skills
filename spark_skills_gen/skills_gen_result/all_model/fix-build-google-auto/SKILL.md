---
id: fix-build-java-dependency-conflict
title: Fix Java Build Failures Caused by Transitive Dependency Version Conflicts
category: fix-build
tags: [java, maven, dependency-management, build-fix, google-auto]
difficulty: medium
success_rate: high
---

## Overview

This skill covers diagnosing and fixing Java/Maven build failures caused by transitive dependency version conflicts — a common class of build errors where a lower-version transitive dependency overrides a higher-version direct dependency, causing compile-time symbol resolution failures.

---

## Module 1: Rapid Diagnosis

### Step 1: Locate and read the build log

```bash
# Find original build logs
find /home/travis/build -name "*.log" | head -10
cat /home/travis/build/<id>-orig.log | tail -150
```

Look for:
- `cannot find symbol` — missing class/method at compile time
- `package X does not exist` — missing or wrong-version dependency
- `ClassNotFoundException` / `NoSuchMethodError` — runtime version mismatch

### Step 2: Identify the failing class and its origin

When you see `cannot find symbol: class Foo` or `method bar() not found`:

1. Search the codebase for the import:
   ```bash
   grep -r "import com.example.Foo" /path/to/repo --include="*.java"
   ```
2. Identify which library provides that class and the minimum version that introduced it.
3. Check Maven's effective dependency tree:
   ```bash
   cd /path/to/module && mvn dependency:tree -Dverbose 2>&1 | grep -A2 -B2 "guava\|<library>"
   ```

### Step 3: Confirm the version conflict

In `dependency:tree` output, look for lines like:
```
[INFO] +- com.google.guava:guava:jar:18.0:test
[INFO] |  \- (com.google.guava:guava:jar:11.0.1:compile - omitted for conflict)
```
or the inverse — where the lower version wins:
```
[INFO] +- com.google.guava:guava:jar:11.0.1:compile
[INFO]    \- (com.google.guava:guava:jar:18.0:test - omitted for conflict)
```

The lower version winning is the root cause pattern.

---

## Module 2: Fix Strategy

### Option A: Add an explicit dependency to force the correct version (preferred)

In the affected module's `pom.xml`, add an explicit dependency with the required version:

```xml
<dependency>
  <groupId>com.google.guava</groupId>
  <artifactId>guava</artifactId>
  <version>18.0</version>  <!-- use the minimum version that provides the needed API -->
  <scope>test</scope>       <!-- match the scope where the failure occurs -->
</dependency>
```

This forces Maven's nearest-wins resolution to pick your explicit declaration over the transitive one.

### Option B: Use `<dependencyManagement>` to pin versions project-wide

In the root or parent `pom.xml`:

```xml
<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>18.0</version>
    </dependency>
  </dependencies>
</dependencyManagement>
```

### Option C: Exclude the conflicting transitive dependency

```xml
<dependency>
  <groupId>com.example</groupId>
  <artifactId>some-lib</artifactId>
  <version>X.Y</version>
  <exclusions>
    <exclusion>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
    </exclusion>
  </exclusions>
</dependency>
```

Use Option C only when you can't upgrade — it's more fragile.

### Writing the patch

Write a standard unified diff:

```bash
cd /path/to/repo
git diff > /home/travis/build/failed/<repo>/<id>/patch_1.diff
```

Or write it manually in unified diff format:
```diff
--- a/module/pom.xml
+++ b/module/pom.xml
@@ -42,6 +42,12 @@
   <dependencies>
+    <dependency>
+      <groupId>com.google.guava</groupId>
+      <artifactId>guava</artifactId>
+      <version>18.0</version>
+      <scope>test</scope>
+    </dependency>
   </dependencies>
```

Apply with:
```bash
git apply /home/travis/build/failed/<repo>/<id>/patch_1.diff
```

---

## Module 3: Verification

### Verify the fix compiles

```bash
cd /path/to/affected/module
mvn test-compile -q 2>&1 | tail -30
```

### Run the full build

```bash
mvn install -DskipTests=false 2>&1 | tail -50
```

Check for `BUILD SUCCESS`.

### Write the analysis note

Always write `/home/travis/build/failed/failed_reasons.txt` before applying the patch — the test suite checks for its existence:

```
Root cause: <library>:<lower-version> was resolved transitively via <transitive-path>,
overriding <library>:<higher-version> needed by <FailingClass>.
Fix: Added explicit <library>:<higher-version> dependency to <module>/pom.xml.
```

---

## Common Pitfalls

- Not running any build command — `test_build_success` checks that the build actually succeeds, not just that a patch file exists. Always run `mvn install` or equivalent after applying the patch.
- Fixing the wrong `pom.xml` — multi-module Maven projects have many `pom.xml` files. The fix must go in the module that owns the failing compilation unit, not the root.
- Wrong scope — if the failing class is only used in tests, use `<scope>test</scope>`. Adding it as `compile` scope can introduce unnecessary runtime dependencies.
- Forgetting `failed_reasons.txt` — the test suite checks `test_note_exists`. Write this file early, before applying patches.
- Forgetting `patch_{i}.diff` — the test suite checks `test_diff_exists`. Always write a diff file even if you apply changes directly.
- Assuming the highest-version dep wins — Maven uses nearest-wins, not highest-wins. A direct dependency at a lower version beats a transitive dependency at a higher version.
- Not verifying with `dependency:tree` — guessing the conflict source wastes time. Always confirm with `mvn dependency:tree -Dverbose`.
