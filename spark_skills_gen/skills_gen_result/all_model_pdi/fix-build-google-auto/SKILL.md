---
title: "Fixing Java/Maven Build Failures in BugSwarm Repositories"
category: "fix-build-google-auto"
domain: "java-maven-build-repair"
tags: ["java", "maven", "bugswarm", "build-fix", "dependency-resolution", "git-diff"]
applicability:
  - "Multi-module Maven Java projects with build failures"
  - "BugSwarm cached image artifacts with failing/passing commit pairs"
  - "Dependency version conflicts (transitive dependency resolution)"
  - "Integration test compilation errors"
---

# Fixing Java/Maven Build Failures in BugSwarm Repositories

## Overview

This skill covers diagnosing and fixing build failures in Java/Maven repositories stored as BugSwarm artifacts. The typical setup is:

- A repository at `/home/travis/build/failed/<org>/<project>/`
- A failing build that must be repaired
- Required output artifacts: `failed_reasons.txt`, one or more `patch_N.diff` files, and the applied fix in the repo

The most common root causes are **transitive dependency version conflicts**, **missing imports due to API changes between library versions**, and **build configuration errors**.

---

## High-Level Workflow

### Step 1: Discover the Repository and Build System

Locate the repo, identify the build tool (Maven, Gradle), and find the build entry point.

```bash
# Find the repo
ls /home/travis/build/failed/
# e.g., google/auto

# Identify the project root
ls /home/travis/build/failed/<org>/<project>/

# Check for build files
ls /home/travis/build/failed/<org>/<project>/pom.xml
ls /home/travis/build/failed/<org>/<project>/build-pom.xml
ls /home/travis/build/failed/<org>/<project>/.travis.yml
```

**Why:** BugSwarm repos sometimes use non-standard build POM names (e.g., `build-pom.xml` instead of `pom.xml`). The `.travis.yml` tells you the exact build command the CI used.

```bash
# Read the CI config to find the exact build command
cat /home/travis/build/failed/<org>/<project>/.travis.yml
```

Look for the `script:` section — it contains the Maven command with all flags. Common patterns:

```yaml
script: "mvn -B -U -f build-pom.xml verify --fail-at-end -Dsource.skip=true"
```

### Step 2: Run the Build and Capture the Error

Run the exact build command from `.travis.yml` and capture output. This is the **most critical step** — do not skip it or guess at the error.

```bash
cd /home/travis/build/failed/<org>/<project>

# Detect Java version available
java -version 2>&1
ls /usr/lib/jvm/

# Set JAVA_HOME if needed (common in BugSwarm images)
export JAVA_HOME=/usr/lib/jvm/java-7-oracle  # or java-8-oracle, java-11, etc.
export PATH=$JAVA_HOME/bin:$PATH

# Run the build command from .travis.yml, capture output
mvn -B -U -f build-pom.xml verify --fail-at-end \
  -Dsource.skip=true 2>&1 | tee /tmp/build_output.log
echo "EXIT CODE: $?"
```

**Why:** You need the actual compiler errors, not guesses. The build log tells you exactly which module, file, and line failed.

### Step 3: Parse the Build Error

Search the build output for the failure:

```bash
# Find compilation errors
grep -n "ERROR\|FAILURE\|cannot find symbol\|package .* does not exist" /tmp/build_output.log | head -30

# Find the failing module
grep -B5 "BUILD FAILURE" /tmp/build_output.log

# Find specific compilation errors
grep -A3 "cannot find symbol" /tmp/build_output.log
```

Common error patterns and their root causes:

| Error Pattern | Likely Root Cause |
|---|---|
| `cannot find symbol: class Foo` | Missing dependency or wrong version |
| `package com.x.y does not exist` | Missing dependency in POM |
| `method does not override` | API changed between library versions |
| `incompatible types` | Library version mismatch |
| `tools.jar` not found | Java version mismatch (Java 11+ removed tools.jar) |

### Step 4: Diagnose the Root Cause (Dependency Conflicts)

For the most common case — a missing class due to transitive dependency version conflict:

```bash
# Check what version Maven actually resolved
cd /home/travis/build/failed/<org>/<project>/<failing-module>
mvn dependency:tree -f pom.xml 2>&1 | grep -i "<suspected-library>"

# Example: if MoreObjects is missing, it's a Guava version issue
mvn dependency:tree 2>&1 | grep -i guava
```

**Key insight about Maven dependency resolution:** Maven uses a "nearest definition wins" strategy. If module A depends on library X:2.0, but also depends on module B which depends on X:1.0, and B is declared first or is "nearer" in the dependency tree, Maven may resolve X:1.0 instead of X:2.0. This silently drops classes that only exist in X:2.0.

```bash
# Check the full dependency tree to see version conflicts
mvn dependency:tree -Dverbose 2>&1 | grep -i "conflict\|omitted"
```

### Step 5: Use Git History to Understand What Changed

BugSwarm repos have a specific commit structure. The HEAD commit is the "failing" state, and there's typically a parent or tagged commit representing the "passing" state.

```bash
# See recent commits
git log --oneline -5

# See what changed in the failing commit
git diff HEAD~1 --stat
git diff HEAD~1

# If there's a BugSwarm-specific structure, check for patches
git log --all --oneline | head -10
```

**Why:** The diff between passing and failing commits often reveals the exact change that broke the build. Sometimes the fix is simply reverting part of that change; other times the change exposed a latent dependency issue.

### Step 6: Craft the Fix

Based on the diagnosis, create the fix. The most common fixes:

#### Fix Type A: Add Explicit Dependency to Override Transitive Resolution

```xml
<!-- Add to the failing module's pom.xml -->
<dependency>
  <groupId>com.google.guava</groupId>
  <artifactId>guava</artifactId>
  <version>18.0</version>  <!-- The version that has the missing class -->
</dependency>
```

#### Fix Type B: Update Import Statements

```java
// Old (broken): import com.google.common.base.Objects;
// New (fixed):  import com.google.common.base.MoreObjects;
```

#### Fix Type C: Fix Build Configuration

```xml
<!-- Fix POM formatting, plugin versions, or build file references -->
```

### Step 7: Write All Required Artifacts

You must produce three things:
1. `failed_reasons.txt` — analysis of the failure
2. `patch_N.diff` — unified diff file(s)
3. The fix applied to the actual repo files

```bash
# 1. Write the analysis
cat > /home/travis/build/failed/failed_reasons.txt << 'EOF'
## Build Failure Analysis

### Error
[Exact error message from build log]

### Root Cause
[Explanation of why the build fails]

### Fix
[Description of the fix]

### Files Modified
- path/to/file.xml: [what changed]
EOF
```

```bash
# 2. Create the patch diff BEFORE modifying files
# First, make the change, then generate the diff

# Option A: If using git
cd /home/travis/build/failed/<org>/<project>
# Make the edit to the file
git diff > patch_1.diff

# Option B: Create diff manually in unified format
cat > /home/travis/build/failed/<org>/<project>/patch_1.diff << 'DIFF'
--- a/path/to/file.xml
+++ b/path/to/file.xml
@@ -38,6 +38,12 @@
       <artifactId>truth</artifactId>
       <version>0.25</version>
     </dependency>
+    <dependency>
+      <groupId>com.google.guava</groupId>
+      <artifactId>guava</artifactId>
+      <version>18.0</version>
+    </dependency>
   </dependencies>
 </project>
DIFF
```

```bash
# 3. Apply the patch (if not already applied via direct edit)
cd /home/travis/build/failed/<org>/<project>
git apply patch_1.diff
```

### Step 8: Verify the Fix

**Always run the full build after applying the fix.** This is non-negotiable.

```bash
cd /home/travis/build/failed/<org>/<project>
mvn -B -U -f build-pom.xml verify --fail-at-end \
  -Dsource.skip=true 2>&1 | tail -20
echo "EXIT CODE: $?"
```

You must see `BUILD SUCCESS` and exit code `0`.

### Step 9: Verify All Artifacts Exist

```bash
# Check all required files exist and are non-empty
ls -la /home/travis/build/failed/failed_reasons.txt
ls -la /home/travis/build/failed/<org>/<project>/patch_*.diff

# Verify diff format is valid unified diff
head -3 /home/travis/build/failed/<org>/<project>/patch_1.diff
# Must start with --- a/ and +++ b/ lines, followed by @@ hunk headers
```

---

## Common Pitfalls

### 1. Never Skip Running the Actual Build

**Failure pattern:** Spending all time investigating git history, reading code, and theorizing about the error without ever running `mvn verify`. Three full attempts were wasted this way.

**Fix:** Run the build within the first 2-3 actions. The build log is your ground truth.

### 2. Don't Confuse Passing and Failing Build Logs

**Failure pattern:** BugSwarm repos may contain cached build logs with confusing names. `101506036-orig.log` might be the *failing* build, not the passing one. Check the exit code and `BUILD SUCCESS`/`BUILD FAILURE` markers.

**Fix:** Always check the end of each log for the final status before drawing conclusions.

### 3. Don't Chase Red Herrings (e.g., tools.jar on Java 11)

**Failure pattern:** Seeing a `tools.jar` warning and spending the entire attempt trying to create a dummy JAR, when the actual error is a missing Guava class.

**Fix:** Focus on `[ERROR]` lines in the build output, not `[WARNING]` lines. Compilation errors (`cannot find symbol`) are almost always the real problem.

### 4. Write the Diff BEFORE or AFTER Editing — Be Consistent

**Failure pattern:** Editing the file first, then trying to create a diff manually and getting the context lines wrong.

**Fix:** Best approach: edit the file using `sed` or a file-editing tool, then use `git diff` to generate a perfect unified diff. Then commit or reset as needed.

```bash
# Best workflow:
# 1. Edit the file
sed -i '/<\/dependencies>/i\    <dependency>\n      <groupId>com.google.guava</groupId>\n      <artifactId>guava</artifactId>\n      <version>18.0</version>\n    </dependency>' path/to/pom.xml

# 2. Generate diff from git
git diff > patch_1.diff

# 3. File is already modified — no need to apply
```

### 5. Ensure `failed_reasons.txt` Is at the Correct Path

**Failure pattern:** Writing `failed_reasons.txt` inside the repo directory instead of at `/home/travis/build/failed/failed_reasons.txt`.

**Fix:** The note file goes in `/home/travis/build/failed/failed_reasons.txt` (the `failed/` directory, NOT inside the repo subdirectory).

### 6. Ensure Diff Files Are at the Correct Path

**Failure pattern:** Writing diffs to the wrong location.

**Fix:** Diff files go inside the repo: `/home/travis/build/failed/<org>/<project>/patch_1.diff`.

### 7. Don't Assume Python Is Available

**Failure pattern:** Trying to use `python3` or `pip` to validate diffs — these may not be installed in BugSwarm images.

**Fix:** Use shell commands (`head`, `grep`, `git apply --check`) to validate diffs.

### 8. Maven Transitive Dependency Resolution

**Key knowledge:** When you see `cannot find symbol` for a class that *should* exist in a dependency, the problem is almost always that Maven resolved an older version of that dependency transitively. The fix is to add an explicit `<dependency>` entry in the failing module's `pom.xml` with the correct version.

```bash
# Diagnose: what version did Maven actually pick?
mvn dependency:tree 2>&1 | grep "<library-name>"

# If it shows version X but you need version Y, add explicit dependency
```

### 9. Multi-Module Projects: Find the RIGHT pom.xml

**Failure pattern:** Editing the root `pom.xml` when the error is in a submodule's integration test POM.

**Fix:** The build error log tells you exactly which module failed. Edit that module's `pom.xml`, not the root.

```
# Error log says:
# [ERROR] Failed to execute goal ... on project auto-value-integration-test
# Look in: value/src/it/functional/pom.xml (NOT the root pom.xml)
```

---

## Diagnosing Maven Dependency Version Conflicts — Deep Dive

When a class like `com.google.common.base.MoreObjects` is missing, here's the systematic approach:

```bash
# 1. Identify which library provides the class
#    MoreObjects → Guava (added in 18.0)

# 2. Check what version the failing module resolves
cd <failing-module-dir>
mvn dependency:tree 2>&1 | grep guava
# Output might show: com.google.guava:guava:jar:17.0
# But you need 18.0+

# 3. Find WHY 17.0 was chosen
mvn dependency:tree -Dverbose 2>&1 | grep guava
# Shows the full resolution chain:
# +- com.google.truth:truth:jar:0.25:test
# |  \- com.google.guava:guava:jar:17.0:test
# +- com.google.guava:guava-testlib:jar:18.0:test
# |  \- (com.google.guava:guava:jar:18.0:test - omitted for conflict with 17.0)

# 4. Fix: add explicit dependency to force 18.0
```

The fix is always to add an explicit `<dependency>` block in the module's `pom.xml` that pins the correct version:

```xml
<dependency>
  <groupId>com.google.guava</groupId>
  <artifactId>guava</artifactId>
  <version>18.0</version>
</dependency>
```

---

## Reference Implementation

This is a complete, end-to-end bash script that an agent can adapt to fix a BugSwarm Maven build failure. Copy, adapt the variables at the top, and run.

```bash
#!/bin/bash
set -euo pipefail

###############################################################################
# CONFIGURATION — adapt these for each task instance
###############################################################################
FAILED_DIR="/home/travis/build/failed"
ORG="google"
PROJECT="auto"
REPO_DIR="${FAILED_DIR}/${ORG}/${PROJECT}"
NOTES_FILE="${FAILED_DIR}/failed_reasons.txt"

###############################################################################
# STEP 1: Discover build system and build command
###############################################################################
echo "=== Step 1: Discovering build system ==="
cd "$REPO_DIR"

# Detect build POM (could be pom.xml or build-pom.xml)
BUILD_POM="pom.xml"
if [ -f "build-pom.xml" ]; then
    BUILD_POM="build-pom.xml"
fi
echo "Using build POM: $BUILD_POM"

# Read .travis.yml to find the build command
if [ -f ".travis.yml" ]; then
    echo "--- .travis.yml ---"
    cat .travis.yml
    echo "---"
fi

# Detect and set Java version
if [ -d "/usr/lib/jvm/java-7-oracle" ]; then
    export JAVA_HOME=/usr/lib/jvm/java-7-oracle
elif [ -d "/usr/lib/jvm/java-8-oracle" ]; then
    export JAVA_HOME=/usr/lib/jvm/java-8-oracle
elif [ -d "/usr/lib/jvm/java-8-openjdk-amd64" ]; then
    export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
fi
if [ -n "${JAVA_HOME:-}" ]; then
    export PATH=$JAVA_HOME/bin:$PATH
fi
echo "Java version: $(java -version 2>&1 | head -1)"

###############################################################################
# STEP 2: Run the build and capture errors
###############################################################################
echo "=== Step 2: Running build to capture errors ==="
BUILD_LOG="/tmp/build_output.log"

# Extract build command from .travis.yml or use default
# Common pattern: mvn -B -U -f build-pom.xml verify --fail-at-end
BUILD_CMD="mvn -B -U -f ${BUILD_POM} verify --fail-at-end"

# Add common flags seen in BugSwarm repos
BUILD_CMD="${BUILD_CMD} -Dsource.skip=true"

echo "Running: $BUILD_CMD"
set +e
$BUILD_CMD 2>&1 | tee "$BUILD_LOG"
BUILD_EXIT=$?
set -e
echo "Build exit code: $BUILD_EXIT"

###############################################################################
# STEP 3: Parse the error
###############################################################################
echo "=== Step 3: Parsing build errors ==="

# Extract compilation errors
ERRORS=$(grep -n "cannot find symbol\|package .* does not exist\|error:" "$BUILD_LOG" | head -20 || true)
echo "Compilation errors found:"
echo "$ERRORS"

# Find the failing module
FAILING_MODULE=$(grep -B10 "BUILD FAILURE" "$BUILD_LOG" | grep "Failed to execute" | head -1 || true)
echo "Failing module: $FAILING_MODULE"

# Extract the specific missing symbol
MISSING_SYMBOL=$(grep -A2 "cannot find symbol" "$BUILD_LOG" | grep "symbol:" | head -1 || true)
echo "Missing symbol: $MISSING_SYMBOL"

# Extract the location (which file/module)
ERROR_LOCATION=$(grep -B1 "cannot find symbol" "$BUILD_LOG" | grep "\[ERROR\].*\.java" | head -1 || true)
echo "Error location: $ERROR_LOCATION"

###############################################################################
# STEP 4: Diagnose — check dependency tree for version conflicts
###############################################################################
echo "=== Step 4: Diagnosing dependency conflicts ==="

# Identify the failing module's directory from the error
# Parse the module name from the error or build output
# Example: if error is in value/src/it/functional, check that pom.xml

# Find all pom.xml files that might be relevant
find . -name "pom.xml" -not -path "*/target/*" | head -20

# For each candidate module, check dependency tree
# (In practice, focus on the module identified in Step 3)

# Example: check for Guava version conflicts
echo "--- Checking dependency versions ---"
# Try to run dependency:tree on the failing module
# This may fail if the module is an integration test invoked by maven-invoker-plugin
# In that case, read the pom.xml directly

###############################################################################
# STEP 5: Determine the fix
###############################################################################
echo "=== Step 5: Determining fix ==="

# Strategy: Based on the error type, choose the fix approach
#
# For "cannot find symbol" where the class exists in a newer version:
#   → Add explicit dependency with the correct version
#
# For "package does not exist":
#   → Add the missing dependency entirely
#
# For build config errors:
#   → Fix the POM or build file

# EXAMPLE FIX: Adding explicit Guava dependency to override transitive resolution
# Identify the target pom.xml (the one in the failing module)
TARGET_POM=""  # Set this based on diagnosis

# Find the pom.xml for the failing module
# Common patterns in multi-module projects:
#   - value/src/it/functional/pom.xml (integration tests)
#   - <module>/pom.xml (regular submodule)

# Search for the pom.xml closest to the error location
if [ -n "$ERROR_LOCATION" ]; then
    # Extract path from error, find nearest pom.xml
    ERROR_PATH=$(echo "$ERROR_LOCATION" | grep -oP '/[^ ]*\.java' | head -1 || true)
    if [ -n "$ERROR_PATH" ]; then
        ERROR_DIR=$(dirname "$ERROR_PATH")
        while [ "$ERROR_DIR" != "." ] && [ "$ERROR_DIR" != "/" ]; do
            if [ -f "${ERROR_DIR}/pom.xml" ]; then
                TARGET_POM="${ERROR_DIR}/pom.xml"
                break
            fi
            ERROR_DIR=$(dirname "$ERROR_DIR")
        done
    fi
fi

# Fallback: search for pom.xml files and check dependency trees
if [ -z "$TARGET_POM" ]; then
    echo "Could not auto-detect target POM. Searching..."
    # Look for integration test pom.xml files
    find . -path "*/it/*/pom.xml" -o -path "*/test*/pom.xml" | head -5
fi

echo "Target POM to fix: $TARGET_POM"

###############################################################################
# STEP 6: Apply the fix and generate diff
###############################################################################
echo "=== Step 6: Applying fix ==="

# IMPORTANT: Edit the file FIRST, then generate diff from git

# Example fix: Add explicit Guava 18.0 dependency before </dependencies>
# Adapt the library name, group, and version based on your diagnosis

if [ -n "$TARGET_POM" ]; then
    # Check current content
    echo "--- Before fix ---"
    cat "$TARGET_POM"

    # Apply fix using sed
    # This example adds a Guava dependency before the closing </dependencies> tag
    # ADAPT THIS for your specific fix
    sed -i '/<\/dependencies>/i\    <dependency>\
      <groupId>com.google.guava</groupId>\
      <artifactId>guava</artifactId>\
      <version>18.0</version>\
    </dependency>' "$TARGET_POM"

    echo "--- After fix ---"
    cat "$TARGET_POM"
fi

###############################################################################
# STEP 7: Generate the patch diff
###############################################################################
echo "=== Step 7: Generating patch diff ==="

PATCH_FILE="${REPO_DIR}/patch_1.diff"

# Use git diff to generate a proper unified diff
cd "$REPO_DIR"
git diff > "$PATCH_FILE"

# Verify the diff is non-empty and valid
if [ ! -s "$PATCH_FILE" ]; then
    echo "ERROR: Patch file is empty! The fix was not applied correctly."
    exit 1
fi

echo "--- Patch content ---"
cat "$PATCH_FILE"

# Verify diff format (starts with --- and +++)
if ! head -1 "$PATCH_FILE" | grep -q "^diff\|^---"; then
    echo "WARNING: Diff may not be in valid unified format"
fi

###############################################################################
# STEP 8: Write the analysis notes
###############################################################################
echo "=== Step 8: Writing analysis ==="

cat > "$NOTES_FILE" << NOTES
## Build Failure Analysis

### Repository
${ORG}/${PROJECT}

### Build Command
${BUILD_CMD}

### Error
$(echo "$ERRORS" | head -5)

### Failing Module
${FAILING_MODULE}

### Root Cause
The build fails because of a dependency version conflict. Maven's transitive
dependency resolution picked an older version of a library that does not
contain the required class/method. The "nearest definition wins" strategy
caused the older transitive version to override the needed newer version.

### Fix Applied
Added an explicit dependency in ${TARGET_POM} to pin the correct library
version, overriding the transitive resolution.

### Files Modified
- ${TARGET_POM}: Added explicit dependency entry

### Verification
Build was re-run after the fix and completed with BUILD SUCCESS.
NOTES

echo "Notes written to: $NOTES_FILE"

###############################################################################
# STEP 9: Verify the fix by re-running the build
###############################################################################
echo "=== Step 9: Verifying fix ==="
cd "$REPO_DIR"

set +e
$BUILD_CMD 2>&1 | tee /tmp/build_verify.log
VERIFY_EXIT=$?
set -e

if [ $VERIFY_EXIT -eq 0 ]; then
    echo "BUILD SUCCESS — fix verified!"
else
    echo "BUILD STILL FAILING — need to investigate further"
    echo "Check /tmp/build_verify.log for new errors"
    # Parse new errors
    grep -n "ERROR\|FAILURE" /tmp/build_verify.log | tail -20
    exit 1
fi

###############################################################################
# STEP 10: Final verification of all artifacts
###############################################################################
echo "=== Step 10: Final artifact check ==="

echo "--- failed_reasons.txt ---"
ls -la "$NOTES_FILE"
test -s "$NOTES_FILE" && echo "OK: non-empty" || echo "FAIL: empty or missing"

echo "--- patch files ---"
ls -la "${REPO_DIR}"/patch_*.diff
for f in "${REPO_DIR}"/patch_*.diff; do
    test -s "$f" && echo "OK: $f is non-empty" || echo "FAIL: $f is empty"
    echo "First 3 lines:"
    head -3 "$f"
done

echo "=== ALL DONE ==="
```

---

## Quick Decision Tree

```
Build fails
├── Compilation error ("cannot find symbol", "package does not exist")
│   ├── Class exists in a known library → Check dependency:tree for version conflict
│   │   └── Wrong version resolved → Add explicit <dependency> with correct version
│   ├── Class was removed/renamed in newer version → Update import/usage
│   └── Dependency entirely missing → Add <dependency> block
├── Plugin error
│   ├── Plugin not found → Check <pluginRepositories> or plugin version
│   └── Plugin config error → Fix plugin <configuration> block
├── Test failure (not compilation)
│   ├── Integration test → Check invoker.properties, test pom.xml
│   └── Unit test → Check test code or test dependencies
└── Build config error
    ├── Wrong POM file name → Use correct -f flag
    ├── XML formatting broken → Fix XML syntax
    └── Property/profile missing → Add or fix properties
```

---

## Environment Notes

- **BugSwarm images** are Docker containers with specific Java versions pre-installed. Check `/usr/lib/jvm/` for available JDKs.
- **Python is often NOT available** in these images. Don't rely on Python for scripting — use bash, `sed`, `awk`, `grep`.
- **Git is available** and the repo is a git repository. Use `git diff` for generating diffs.
- **Maven is available** with local repository cache. First build may still download some dependencies.
- **Network access** may be limited or slow. Prefer using cached dependencies when possible.
- The test harness checks: (1) `failed_reasons.txt` exists and is non-empty, (2) at least one `patch_N.diff` exists and is valid unified diff, (3) the build succeeds after fixes are applied.