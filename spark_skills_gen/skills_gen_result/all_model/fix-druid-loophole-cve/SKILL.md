---
id: fix-druid-loophole-cve
title: Patching Apache Druid JavaScript CVE (Arbitrary Code Execution via Jackson Injection Bypass)
version: 1.0.0
tags: [java, security, cve, apache-druid, jackson, deserialization, patch]
---

## Overview

This skill covers patching Apache Druid vulnerabilities where attackers bypass JavaScript security settings via Jackson's `@JacksonInject` mechanism. The canonical exploit sends an empty-key JSON property (`"": {"enabled": true}`) to override Guice-injected disabled configs, enabling arbitrary JavaScript execution.

The root cause is in Jackson's `PropertyBasedCreator`: it registers `@JacksonInject` parameters using an empty string `""` as their lookup key. An attacker-controlled JSON property with key `""` matches this and injects a malicious config object, bypassing the server's Guice-managed disabled state.

---

## Module 1: Understand the Exploit Path Before Writing Any Code

Before touching source files, trace the full exploit chain. Guessing at the fix wastes build cycles.

### Step 1 — Confirm the injection mechanism

```bash
# Find the annotation introspector used for Guice/Jackson integration
find /root/druid -type f -name "*.java" | xargs grep -l "GuiceAnnotationIntrospector\|JacksonInject\|InjectableValues" 2>/dev/null | grep -v test
```

The key class is typically `GuiceAnnotationIntrospector` (or equivalent). It bridges Guice DI with Jackson deserialization.

### Step 2 — Confirm existing `isEnabled()` guards exist but are bypassable

```bash
# Check that JavaScript components already call isEnabled()
grep -n "isEnabled\|JavaScriptConfig" /root/druid/processing/src/main/java/org/apache/druid/js/JavaScriptConfig.java
grep -rn "isEnabled()" /root/druid/processing/src/main/java/org/apache/druid/query/filter/JavaScriptDimFilter.java
```

If `isEnabled()` checks exist but the exploit still works, the problem is upstream — the config object itself is being replaced before `isEnabled()` is ever called.

### Step 3 — Identify the Jackson version and available fix APIs

```bash
find /root/.m2 -name "jackson-databind-*.jar" 2>/dev/null | sort | tail -3
# Check if findPropertyIgnorals() is available in this Jackson version
jar tf <path-to-jackson-databind.jar> | grep AnnotationIntrospector
```

The fix strategy depends on what Jackson APIs are available. For Jackson 2.x, `findPropertyIgnorals()` on `AnnotationIntrospector` is the correct hook.

---

## Module 2: Implement the Fix in `GuiceAnnotationIntrospector`

The correct fix is to override `findPropertyIgnorals()` in `GuiceAnnotationIntrospector` to tell Jackson to ignore any JSON property with an empty name `""`. This blocks the exploit while leaving normal Guice injection intact.

### Locate the file

```bash
find /root/druid -name "GuiceAnnotationIntrospector.java" | grep -v test
```

### Add the `findPropertyIgnorals` override

Inside the `GuiceAnnotationIntrospector` class, add:

```java
@Override
public JsonIgnoreProperties.Value findPropertyIgnorals(Annotated ac)
{
    // Block the CVE exploit: Jackson registers @JacksonInject params with ""
    // as their property name. An attacker can send {"": {"enabled": true}} to
    // inject a malicious config. Ignoring the empty-string property prevents this.
    if (ac instanceof AnnotatedConstructor) {
        AnnotatedConstructor constructor = (AnnotatedConstructor) ac;
        for (int i = 0; i < constructor.getParameterCount(); i++) {
            if (constructor.getParameterAnnotation(i, JacksonInject.class) != null) {
                return JsonIgnoreProperties.Value.forIgnoredProperties("");
            }
        }
    }
    return super.findPropertyIgnorals(ac);
}
```

Required imports (add to the file's import block):

```java
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.introspect.AnnotatedConstructor;
```

### Write the patch file

```bash
cd /root/druid
git diff > /root/patches/cve-fix.patch
# Verify the patch is non-empty and targets the right file
cat /root/patches/cve-fix.patch | head -20
```

### Apply and verify

```bash
cd /root/druid
git apply /root/patches/cve-fix.patch
# Confirm the change landed
grep -n "findPropertyIgnorals\|forIgnoredProperties" \
  $(find . -name "GuiceAnnotationIntrospector.java" | grep -v test)
```

---

## Module 3: Build and Validate

### Build command (skip web-console to avoid OOM, skip quality checks for patched files)

```bash
cd /root/druid
mvn clean package -DskipTests \
  -Dcheckstyle.skip=true \
  -Dpmd.skip=true \
  -Dforbiddenapis.skip=true \
  -Dspotbugs.skip=true \
  -Danimal.sniffer.skip=true \
  -Denforcer.skip=true \
  -Djacoco.skip=true \
  -Ddependency-check.skip=true \
  -pl '!web-console' \
  -pl indexing-service -am 2>&1 | tail -30
```

### Confirm the JAR contains the fix

```bash
# Find the built JAR containing GuiceAnnotationIntrospector
find /root/druid -name "*.jar" -newer /root/patches/cve-fix.patch | \
  xargs -I{} sh -c 'jar tf {} 2>/dev/null | grep -q GuiceAnnotation && echo {}'

# Decompile to verify the method is present
javap -c <path-to-jar-with-class> 2>/dev/null | grep -A5 "findPropertyIgnorals"
```

### Quick smoke test before deploying

```bash
# Simulate the exploit payload structure — should be rejected after fix
curl -s -X POST http://localhost:8888/druid/indexer/v1/sampler \
  -H "Content-Type: application/json" \
  -d '{"type":"index","spec":{"dataSchema":{"transformSpec":{"filter":{"type":"javascript","function":"function(){return true;}","":{"enabled":true}}}}}}' \
  | python3 -m json.tool | grep -i "error\|exception\|unsupported"
```

---

## Common Pitfalls

**Pitfall 1 — Adding structural helpers without enforcement**
Adding a `getEnabledInstance()` static helper or modifying `JavaScriptConfig` structurally does nothing if the injected object is replaced before any check runs. The fix must prevent the malicious injection at the Jackson layer, not downstream.

**Pitfall 2 — Fixing `isEnabled()` call sites instead of the injection point**
The `isEnabled()` guards in `JavaScriptDimFilter`, `JavaScriptExtractionFn`, etc. are correct and necessary, but they are not the vulnerability. The exploit replaces the config object entirely, so those guards never see a disabled config. Fix the source of injection, not the consumers.

**Pitfall 3 — Forgetting to handle the `AnnotatedConstructor` case specifically**
The `findPropertyIgnorals` override must check for `AnnotatedConstructor` instances with `@JacksonInject` parameters. A generic override that always returns ignored properties will break legitimate deserialization of other types.

**Pitfall 4 — Not writing the patch file before building**
The verifier checks for a patch file in `/root/patches/`. Run `git diff > /root/patches/<name>.patch` before `mvn` so the diff captures only your changes, not build artifacts.

**Pitfall 5 — Building the wrong module**
The `GuiceAnnotationIntrospector` lives in a core/common module. Use `-pl indexing-service -am` to build it and all its dependencies. Building only `indexing-service` without `-am` will miss the module containing your fix.

**Pitfall 6 — Assuming the exploit is in the JavaScript execution path**
The exploit fires during JSON deserialization, before any JavaScript engine is invoked. Tracing the exploit through `ScriptEngine` or Rhino is a dead end. Focus on Jackson's constructor injection pipeline.
