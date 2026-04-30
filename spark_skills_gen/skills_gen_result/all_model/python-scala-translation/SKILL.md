---
name: python-to-scala-module-translation
description: Translate a Python module into idiomatic Scala 2.13 while preserving the required API surface, behavior, and compile/test compatibility. Use this for tasks that require converting Python classes/functions into maintainable Scala source files suitable for JVM and distributed-system codebases.
license: CC-BY-4.0
tags:
  - scala
  - python
  - translation
  - scala-2.13
  - api-parity
  - refactoring
  - sbt
  - scalac
  - distributed-systems
---

# Python to Scala Module Translation

This skill is for tasks where you are given a Python source file and must produce a Scala 2.13 implementation with the same public API and behavior, while making it idiomatic Scala rather than doing a line-by-line rewrite.

The most common success pattern is:

1. **Inventory the Python API exactly**
2. **Model domain types using Scala ADTs/case classes/traits**
3. **Translate behavior, not syntax**
4. **Handle absence and errors using Scala conventions**
5. **Put the code in the package/layout expected by the evaluator**
6. **Compile early and fix type/inheritance mismatches before polishing**
7. **Verify names, signatures, constructors, and helper functions before finalizing**

---

## When to Use This Skill

Use this skill when the task asks you to:

- translate a Python `.py` module into Scala
- preserve a required class/function list
- target Scala 2.13
- follow Scala best practices instead of literal translation
- support downstream compilation via `scalac` or `sbt`
- prepare code for large-scale or distributed processing environments

Typical examples:

- tokenizers
- data transformation utilities
- schema wrappers
- adapters with builder patterns
- normalization/parsing layers

---

## High-Level Workflow

### 1) Inspect the Python source and extract the public contract
**Why:** Translation failures usually come from missing a required class, wrong function name, constructor mismatch, or changing behavior subtly.

Create an API inventory before writing Scala:

- classes
- enums/constants
- top-level functions
- constructor parameters and defaults
- public methods
- return types and data shapes
- error behavior
- mutability assumptions

#### Command workflow
```bash
#!/usr/bin/env bash
set -euo pipefail

PY_FILE="${1:-/root/Module.py}"

if [[ ! -f "$PY_FILE" ]]; then
  echo "Python file not found: $PY_FILE" >&2
  exit 1
fi

echo "== Public class/function inventory =="
python3 - <<'PY' "$PY_FILE"
import ast
import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text()
tree = ast.parse(src)

for node in tree.body:
    if isinstance(node, ast.ClassDef):
        print(f"class {node.name}")
        for child in node.body:
            if isinstance(child, ast.FunctionDef):
                args = [a.arg for a in child.args.args]
                print(f"  def {child.name}({', '.join(args)})")
    elif isinstance(node, ast.FunctionDef):
        args = [a.arg for a in node.args.args]
        print(f"def {node.name}({', '.join(args)})")
PY
```

#### What to look for
- Whether Python uses `Enum`, dataclasses, plain classes, or dicts
- Whether top-level helper functions mirror methods
- Whether defaults are optional or nullable
- Whether the code expects inheritance or composition

---

### 2) Determine package/layout expectations before coding
**Why:** Even a correct Scala implementation can fail if it lands in the wrong package or file structure.

Check:

- whether tests import a package like `tokenizer.Tokenizer`
- whether evaluator copies the file into `src/main/scala/<package>/...`
- whether a package declaration is required
- whether source/object/class names must match the file name

#### Inspect build/test context
```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root}"

echo "== Candidate Scala build/test files =="
find "$ROOT" -maxdepth 3 \( -name "build.sbt" -o -name "*.scala" -o -name "pom.xml" \) -print | sort

echo
echo "== Package/import hints from Scala tests =="
grep -RIn --include='*.scala' --include='build.sbt' \
  -E 'package |import .*|extends .*Spec|AnyFunSuite|FlatSpec|FunSuite' "$ROOT" || true
```

#### Decision criteria
- If tests import `packageName.Symbol`, add `package packageName`
- If the file will be copied into a package directory, match that package
- Prefer one top-level `object` for utility functions if Python had module-level functions

---

### 3) Map Python constructs to idiomatic Scala constructs
**Why:** Literal translations are usually unidiomatic and fragile.

Use these mappings:

| Python | Scala 2.13 |
|---|---|
| `Enum` | `sealed trait` + `case object`s |
| `dataclass` / simple record | `final case class` |
| base class with overridable methods | `trait` or `abstract class` |
| `None` | `Option` |
| dict metadata | `Map[String, String]` or `Map[String, Any]` depending on task |
| free functions | methods on companion object or top-level object members |
| exceptions | `IllegalArgumentException`, `NoSuchElementException`, or `Try`/`Either` where appropriate |

#### Example: enum/data model translation
```scala
package example

sealed trait TokenType {
  def name: String
}

object TokenType {
  case object StringType extends TokenType { val name = "string" }
  case object NumericType extends TokenType { val name = "numeric" }
  case object TemporalType extends TokenType { val name = "temporal" }
  case object UnknownType extends TokenType { val name = "unknown" }

  val all: Vector[TokenType] =
    Vector(StringType, NumericType, TemporalType, UnknownType)

  def fromName(value: String): TokenType = {
    val normalized = Option(value).map(_.trim.toLowerCase).getOrElse("")
    all.find(_.name == normalized).getOrElse(UnknownType)
  }
}

final case class Token(
  value: String,
  tokenType: TokenType,
  metadata: Map[String, String] = Map.empty
) {
  def withMetadata(entries: Map[String, String]): Token =
    copy(metadata = metadata ++ entries)
}
```

#### Notes
- Prefer `sealed trait` for exhaustiveness and pattern matching
- Prefer immutable `case class` with copy/update helpers
- Avoid Java-style mutable beans unless the API explicitly requires mutation

---

### 4) Translate behavior class-by-class, preserving the API surface
**Why:** The evaluator usually checks exact names and expected behavior, not your preferred redesign.

Build Scala types for every required Python class and function, even if some delegate internally.

#### Example: tokenizer abstraction
```scala
package example

trait BaseTokenizer[-T] {
  def tokenize(value: T): Token

  def tokenizeBatch(values: Iterable[T]): Vector[Token] = {
    if (values == null) Vector.empty
    else values.iterator.map(tokenize).toVector
  }

  def toToken(value: T): Token = tokenize(value)
}
```

#### Why this shape works
- Contravariance (`-T`) is often useful for consumer-style abstractions
- Batch helpers can live on the trait
- Alias functions like `toToken` preserve Python helper names without duplication

---

### 5) Translate null/None/absence handling explicitly
**Why:** Python often tolerates `None` and mixed types; Scala code must represent that safely.

General rules:

- Use `Option(value)` when accepting Java/Python-like nullable inputs
- Normalize `null` before calling methods
- Return empty collections instead of `null`
- If the Python code accepted âanyâ input, use pattern matching in a dispatcher object/class

#### Example: safe normalization
```scala
package example

import java.time.temporal.TemporalAccessor

final class UniversalTokenizer(
  stringTokenizer: BaseTokenizer[String],
  numericTokenizer: BaseTokenizer[BigDecimal],
  temporalTokenizer: BaseTokenizer[TemporalAccessor]
) {

  def tokenize(value: Any): Token = value match {
    case null =>
      Token("", TokenType.UnknownType, Map("input" -> "null"))

    case s: String =>
      stringTokenizer.tokenize(s)

    case i: Int =>
      numericTokenizer.tokenize(BigDecimal(i))

    case l: Long =>
      numericTokenizer.tokenize(BigDecimal(l))

    case d: Double if !d.isNaN && !d.isInfinity =>
      numericTokenizer.tokenize(BigDecimal.decimal(d))

    case bd: BigDecimal =>
      numericTokenizer.tokenize(bd)

    case t: TemporalAccessor =>
      temporalTokenizer.tokenize(t)

    case other =>
      Token(other.toString, TokenType.UnknownType, Map("class" -> other.getClass.getName))
  }

  def tokenizeBatch(values: Iterable[Any]): Vector[Token] =
    Option(values).map(_.iterator.map(tokenize).toVector).getOrElse(Vector.empty)
}
```

#### Key point
Prefer a single dispatch point for heterogeneous input rather than scattered `isInstanceOf` checks throughout the codebase.

---

### 6) Handle formatting and numeric/temporal edge cases carefully
**Why:** Translation often breaks on formatting details, especially numbers and dates.

For numeric code:

- avoid binary floating surprises where possible
- preserve precision semantics intentionally
- guard against `NaN`/infinite values
- if rounding is required, specify it explicitly

#### Numeric example
```scala
package example

import scala.math.BigDecimal.RoundingMode

final class NumericTokenizer(
  precision: Int = 2,
  stripTrailingZeros: Boolean = false
) extends BaseTokenizer[BigDecimal] {

  require(precision >= 0, s"precision must be >= 0, got $precision")

  override def tokenize(value: BigDecimal): Token = {
    val scaled = value.setScale(precision, RoundingMode.HALF_UP)
    val rendered =
      if (stripTrailingZeros) scaled.bigDecimal.stripTrailingZeros.toPlainString
      else scaled.bigDecimal.toPlainString

    Token(
      value = rendered,
      tokenType = TokenType.NumericType,
      metadata = Map("precision" -> precision.toString)
    )
  }
}
```

For temporal code:

- use `java.time`
- prefer explicit formatter handling
- support a default ISO representation if no format string is provided

#### Temporal example
```scala
package example

import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAccessor
import scala.util.Try

final class TemporalTokenizer(formatStr: Option[String] = None) extends BaseTokenizer[TemporalAccessor] {
  private val formatterOpt: Option[DateTimeFormatter] =
    formatStr.filter(_.trim.nonEmpty).map(DateTimeFormatter.ofPattern)

  override def tokenize(value: TemporalAccessor): Token = {
    val rendered = formatterOpt match {
      case Some(formatter) =>
        Try(formatter.format(value)).getOrElse {
          throw new IllegalArgumentException("Unable to format temporal value with the provided pattern")
        }
      case None =>
        value.toString
    }

    Token(
      value = rendered,
      tokenType = TokenType.TemporalType,
      metadata = formatStr.map(fmt => Map("format" -> fmt)).getOrElse(Map.empty)
    )
  }
}
```

---

### 7) Recreate builder patterns idiomatically, not mechanically
**Why:** Python builders often mutate internal state. In Scala, immutable fluent builders are usually clearer and safer.

#### Example: immutable builder
```scala
package example

import java.time.temporal.TemporalAccessor

final class TokenizerBuilder private (
  private val stringTokenizer: BaseTokenizer[String],
  private val numericTokenizer: BaseTokenizer[BigDecimal],
  private val temporalTokenizer: BaseTokenizer[TemporalAccessor]
) {

  def withStringTokenizer(tokenizer: BaseTokenizer[String]): TokenizerBuilder =
    new TokenizerBuilder(tokenizer, numericTokenizer, temporalTokenizer)

  def withNumericTokenizer(tokenizer: BaseTokenizer[BigDecimal]): TokenizerBuilder =
    new TokenizerBuilder(stringTokenizer, tokenizer, temporalTokenizer)

  def withTemporalTokenizer(tokenizer: BaseTokenizer[TemporalAccessor]): TokenizerBuilder =
    new TokenizerBuilder(stringTokenizer, numericTokenizer, tokenizer)

  def build(): UniversalTokenizer =
    new UniversalTokenizer(stringTokenizer, numericTokenizer, temporalTokenizer)
}

object TokenizerBuilder {
  def default(): TokenizerBuilder =
    new TokenizerBuilder(
      new StringTokenizer(),
      new NumericTokenizer(),
      new TemporalTokenizer()
    )
}
```

#### Benefits
- avoids mutation bugs
- reads fluently in tests
- keeps defaults centralized

---

### 8) Preserve required top-level helpers through a companion/object faÃ§ade
**Why:** Python modules commonly expose free functions. Scala should expose equivalent entry points via an `object`.

#### Example object faÃ§ade
```scala
package example

import java.time.temporal.TemporalAccessor

object Tokenizer {
  private val defaultTokenizer: UniversalTokenizer =
    TokenizerBuilder.default().build()

  def tokenize(value: Any): Token =
    defaultTokenizer.tokenize(value)

  def tokenizeBatch(values: Iterable[Any]): Vector[Token] =
    defaultTokenizer.tokenizeBatch(values)

  def toToken(value: Any): Token =
    tokenize(value)

  def withMetadata(token: Token, metadata: Map[String, String]): Token =
    token.withMetadata(metadata)
}
```

#### Final verification checklist for API parity
- Required type names present
- Required method names present
- Required function names present
- Constructor defaults preserved
- Return types compile against expected tests

---

### 9) Compile early with both `scalac` and `sbt` if possible
**Why:** `scalac` catches syntax/type errors quickly; `sbt` catches package/project integration issues.

#### Quick compile workflow
```bash
#!/usr/bin/env bash
set -euo pipefail

SCALA_FILE="${1:-/root/Module.scala}"
TMP_DIR="$(mktemp -d)"
mkdir -p "$TMP_DIR/classes"

if ! command -v scalac >/dev/null 2>&1; then
  echo "scalac is not installed" >&2
  exit 1
fi

echo "Compiling with scalac..."
scalac -d "$TMP_DIR/classes" "$SCALA_FILE"

echo "Compilation succeeded."
```

#### Temporary sbt project workflow
```bash
#!/usr/bin/env bash
set -euo pipefail

SRC_FILE="${1:-/root/Module.scala}"
PKG_DIR="${2:-example}"

TMP_DIR="$(mktemp -d)"
mkdir -p "$TMP_DIR/src/main/scala/$PKG_DIR"

cp "$SRC_FILE" "$TMP_DIR/src/main/scala/$PKG_DIR/$(basename "$SRC_FILE")"

cat > "$TMP_DIR/build.sbt" <<'SBT'
ThisBuild / scalaVersion := "2.13.12"
lazy val root = (project in file("."))
SBT

echo "Running sbt compile..."
(
  cd "$TMP_DIR"
  sbt -no-colors compile
)
```

#### What compile failures usually mean
- variance issue in trait/class hierarchy
- constructor or default-value mismatch
- Java time imports missing
- package mismatch
- overloaded method ambiguity
- type too specific for heterogeneous dispatch

---

### 10) Validate behavior with focused smoke tests
**Why:** Translation can compile while still violating semantic expectations.

Create small checks for:

- string tokenization
- numeric formatting/rounding
- temporal formatting
- mixed batch dispatch
- metadata updates
- null/empty handling

#### Example smoke test runner
```bash
#!/usr/bin/env bash
set -euo pipefail

TMP_DIR="$(mktemp -d)"
mkdir -p "$TMP_DIR/src/main/scala/example"

cp /root/Tokenizer.scala "$TMP_DIR/src/main/scala/example/Tokenizer.scala"

cat > "$TMP_DIR/build.sbt" <<'SBT'
ThisBuild / scalaVersion := "2.13.12"
lazy val root = (project in file("."))
SBT

cat > "$TMP_DIR/src/main/scala/example/Smoke.scala" <<'SCALA'
package example

import java.time.LocalDate

object Smoke extends App {
  val token1 = Tokenizer.tokenize("hello")
  assert(token1.value == "hello")

  val token2 = Tokenizer.tokenize(BigDecimal("12.345"))
  assert(token2.value.nonEmpty)

  val token3 = Tokenizer.tokenize(LocalDate.of(2024, 1, 2))
  assert(token3.value.nonEmpty)

  val batch = Tokenizer.tokenizeBatch(List("x", 1, LocalDate.of(2024, 1, 2)))
  assert(batch.size == 3)

  val token4 = Tokenizer.withMetadata(token1, Map("lang" -> "en"))
  assert(token4.metadata.get("lang").contains("en"))

  println("Smoke checks passed")
}
SCALA

(
  cd "$TMP_DIR"
  sbt -no-colors "runMain example.Smoke"
)
```

---

## Domain Conventions for Python â Scala Translation

### Prefer these Scala constructs
- `sealed trait` + `case object` for discriminated types
- `final case class` for immutable records
- `trait` for interfaces/abstract behavior
- companion `object` for factory/utilities
- `Option`, `Try`, `Either` for nullable/fallible flows
- immutable collections: `Vector`, `List`, `Map`

### Avoid these unless required
- pervasive `null`
- generic `Exception`
- mutable public state
- Java collections unless interop demands them
- one-to-one syntax translation of Python internals

### Naming conventions
- Types/classes/traits/objects: `PascalCase`
- methods/vals: `camelCase`
- constants in objects: `camelCase` or `PascalCase` for case objects
- booleans: `isX`, `hasX`, `canX` where appropriate

### Error handling conventions
- `require(...)` for constructor preconditions
- `IllegalArgumentException` for invalid inputs
- `NoSuchElementException` only when lookup failure is truly the issue
- `Try` for formatting/parsing boundaries when failure is expected

---

## Common Pitfalls

### 1) Missing the required API surface
If the task explicitly requires names like `TokenType`, `Token`, `BaseTokenizer`, `TokenizerBuilder`, `tokenize`, `tokenizeBatch`, `toToken`, `withMetadata`, you must define them exactly.

**Avoid:** âCleanerâ renames that break tests.

### 2) Forgetting the package declaration
If tests expect imports from a package, omitting `package ...` will fail compilation or test resolution.

**Check tests/build layout before coding.**

### 3) Literal Python-to-Scala inheritance
Python base classes sometimes map better to a Scala `trait` than an abstract class. Choose the smallest abstraction that preserves behavior.

### 4) Overly narrow numeric types
A tokenizer/normalizer that handles heterogeneous input often should accept `BigDecimal` internally and dispatch from `Int`, `Long`, `Double`, etc.

**Avoid:** locking the API to a single primitive if Python accepted multiple numeric forms.

### 5) Unclear rounding/formatting semantics
If numbers are formatted, specify scale and rounding explicitly. If dates are formatted, define formatter behavior clearly.

### 6) Using generic exceptions everywhere
Prefer descriptive `IllegalArgumentException` or Scala-safe wrappers rather than `throw new Exception(...)`.

### 7) Translating `None`/`null` unsafely
Python code may tolerate absent values. Scala should normalize nullable inputs with `Option(...)` and return explicit empty/default outputs where appropriate.

### 8) Compiling too late
Compile immediately after writing the core model and again after adding builders/helpers. This catches variance and constructor issues early.

### 9) Ignoring helper functions that mirror methods
Python modules often expose both instance methods and module-level helpers. Tests frequently call the helpers.

### 10) Rewriting too aggressively
You should make the code idiomatic Scala, but not redesign away required behavior or public names.

---

## Reference Implementation

The following is a complete, runnable Scala 2.13 example that demonstrates an idiomatic translation pattern for a Python tokenizer-like module. It includes:

- ADT for token types
- immutable token model
- base tokenizer trait
- string, numeric, temporal, universal, and whitespace tokenizers
- immutable builder
- top-level object helpers
- null handling
- metadata enrichment
- batch tokenization

Adapt names/behavior to the source Python module you are translating.

```scala
package tokenizer

import java.time.format.DateTimeFormatter
import java.time.temporal.TemporalAccessor
import scala.math.BigDecimal.RoundingMode
import scala.util.Try

sealed trait TokenType {
  def name: String
}

object TokenType {
  case object StringType extends TokenType { val name: String = "string" }
  case object NumericType extends TokenType { val name: String = "numeric" }
  case object TemporalType extends TokenType { val name: String = "temporal" }
  case object WhitespaceType extends TokenType { val name: String = "whitespace" }
  case object UnknownType extends TokenType { val name: String = "unknown" }

  val values: Vector[TokenType] =
    Vector(StringType, NumericType, TemporalType, WhitespaceType, UnknownType)

  def fromName(raw: String): TokenType = {
    val normalized = Option(raw).map(_.trim.toLowerCase).getOrElse("")
    values.find(_.name == normalized).getOrElse(UnknownType)
  }
}

final case class Token(
  value: String,
  tokenType: TokenType,
  metadata: Map[String, String] = Map.empty
) {
  def withMetadata(entries: Map[String, String]): Token = {
    val safeEntries = Option(entries).getOrElse(Map.empty[String, String])
    copy(metadata = metadata ++ safeEntries)
  }
}

trait BaseTokenizer[-T] {
  def tokenize(value: T): Token

  def tokenizeBatch(values: Iterable[T]): Vector[Token] =
    Option(values).map(_.iterator.map(tokenize).toVector).getOrElse(Vector.empty)

  def toToken(value: T): Token =
    tokenize(value)
}

final class StringTokenizer(
  trim: Boolean = false,
  lowerCase: Boolean = false
) extends BaseTokenizer[String] {

  override def tokenize(value: String): Token = {
    val base = Option(value).getOrElse("")
    val normalized =
      if (lowerCase) {
        val s = if (trim) base.trim else base
        s.toLowerCase
      } else if (trim) {
        base.trim
      } else {
        base
      }

    Token(
      value = normalized,
      tokenType = TokenType.StringType,
      metadata = Map("length" -> normalized.length.toString)
    )
  }
}

final class NumericTokenizer(
  precision: Int = 2,
  stripTrailingZeros: Boolean = false
) extends BaseTokenizer[BigDecimal] {

  require(precision >= 0, s"precision must be >= 0, got $precision")

  override def tokenize(value: BigDecimal): Token = {
    val scaled = value.setScale(precision, RoundingMode.HALF_UP)
    val rendered =
      if (stripTrailingZeros) scaled.bigDecimal.stripTrailingZeros.toPlainString
      else scaled.bigDecimal.toPlainString

    Token(
      value = rendered,
      tokenType = TokenType.NumericType,
      metadata = Map("precision" -> precision.toString)
    )
  }
}

final class TemporalTokenizer(formatStr: Option[String] = None) extends BaseTokenizer[TemporalAccessor] {
  private val formatterOpt: Option[DateTimeFormatter] =
    formatStr.filter(_.trim.nonEmpty).map(DateTimeFormatter.ofPattern)

  override def tokenize(value: TemporalAccessor): Token = {
    if (value == null) {
      return Token("", TokenType.TemporalType, Map("warning" -> "null temporal"))
    }

    val rendered = formatterOpt match {
      case Some(formatter) =>
        Try(formatter.format(value)).getOrElse {
          throw new IllegalArgumentException("Unable to format temporal value with the provided pattern")
        }
      case None =>
        value.toString
    }

    Token(
      value = rendered,
      tokenType = TokenType.TemporalType,
      metadata = formatStr.map(fmt => Map("format" -> fmt)).getOrElse(Map.empty)
    )
  }
}

final class WhitespaceTokenizer(
  collapseInternalWhitespace: Boolean = true,
  trimEdges: Boolean = true
) extends BaseTokenizer[String] {

  private val whitespaceRegex = "\\s+".r

  override def tokenize(value: String): Token = {
    val raw = Option(value).getOrElse("")
    val normalized0 = if (trimEdges) raw.trim else raw
    val normalized =
      if (collapseInternalWhitespace) whitespaceRegex.replaceAllIn(normalized0, " ")
      else normalized0

    Token(
      value = normalized,
      tokenType = TokenType.WhitespaceType,
      metadata = Map("length" -> normalized.length.toString)
    )
  }
}

final class UniversalTokenizer(
  stringTokenizer: BaseTokenizer[String],
  numericTokenizer: BaseTokenizer[BigDecimal],
  temporalTokenizer: BaseTokenizer[TemporalAccessor],
  whitespaceTokenizer: BaseTokenizer[String]
) {

  private def isWhitespaceHeavy(s: String): Boolean =
    s.exists(_.isWhitespace)

  def tokenize(value: Any): Token = value match {
    case null =>
      Token("", TokenType.UnknownType, Map("input" -> "null"))

    case s: String if isWhitespaceHeavy(s) =>
      whitespaceTokenizer.tokenize(s)

    case s: String =>
      stringTokenizer.tokenize(s)

    case bd: BigDecimal =>
      numericTokenizer.tokenize(bd)

    case bi: BigInt =>
      numericTokenizer.tokenize(BigDecimal(bi))

    case i: Int =>
      numericTokenizer.tokenize(BigDecimal(i))

    case l: Long =>
      numericTokenizer.tokenize(BigDecimal(l))

    case f: Float if !f.isNaN && !f.isInfinity =>
      numericTokenizer.tokenize(BigDecimal.decimal(f.toDouble))

    case d: Double if !d.isNaN && !d.isInfinity =>
      numericTokenizer.tokenize(BigDecimal.decimal(d))

    case t: TemporalAccessor =>
      temporalTokenizer.tokenize(t)

    case other =>
      Token(
        value = other.toString,
        tokenType = TokenType.UnknownType,
        metadata = Map("class" -> other.getClass.getName)
      )
  }

  def tokenizeBatch(values: Iterable[Any]): Vector[Token] =
    Option(values).map(_.iterator.map(tokenize).toVector).getOrElse(Vector.empty)

  def toToken(value: Any): Token =
    tokenize(value)

  def withMetadata(token: Token, metadata: Map[String, String]): Token =
    token.withMetadata(metadata)
}

final class TokenizerBuilder private (
  private val stringTokenizer: BaseTokenizer[String],
  private val numericTokenizer: BaseTokenizer[BigDecimal],
  private val temporalTokenizer: BaseTokenizer[TemporalAccessor],
  private val whitespaceTokenizer: BaseTokenizer[String]
) {

  def withStringTokenizer(tokenizer: BaseTokenizer[String]): TokenizerBuilder =
    new TokenizerBuilder(tokenizer, numericTokenizer, temporalTokenizer, whitespaceTokenizer)

  def withNumericTokenizer(tokenizer: BaseTokenizer[BigDecimal]): TokenizerBuilder =
    new TokenizerBuilder(stringTokenizer, tokenizer, temporalTokenizer, whitespaceTokenizer)

  def withTemporalTokenizer(tokenizer: BaseTokenizer[TemporalAccessor]): TokenizerBuilder =
    new TokenizerBuilder(stringTokenizer, numericTokenizer, tokenizer, whitespaceTokenizer)

  def withWhitespaceTokenizer(tokenizer: BaseTokenizer[String]): TokenizerBuilder =
    new TokenizerBuilder(stringTokenizer, numericTokenizer, temporalTokenizer, tokenizer)

  def build(): UniversalTokenizer =
    new UniversalTokenizer(
      stringTokenizer = stringTokenizer,
      numericTokenizer = numericTokenizer,
      temporalTokenizer = temporalTokenizer,
      whitespaceTokenizer = whitespaceTokenizer
    )
}

object TokenizerBuilder {
  def default(): TokenizerBuilder =
    new TokenizerBuilder(
      stringTokenizer = new StringTokenizer(),
      numericTokenizer = new NumericTokenizer(),
      temporalTokenizer = new TemporalTokenizer(),
      whitespaceTokenizer = new WhitespaceTokenizer()
    )
}

object Tokenizer {
  private val defaultTokenizer: UniversalTokenizer =
    TokenizerBuilder.default().build()

  def tokenize(value: Any): Token =
    defaultTokenizer.tokenize(value)

  def tokenizeBatch(values: Iterable[Any]): Vector[Token] =
    defaultTokenizer.tokenizeBatch(values)

  def toToken(value: Any): Token =
    defaultTokenizer.toToken(value)

  def withMetadata(token: Token, metadata: Map[String, String]): Token =
    defaultTokenizer.withMetadata(token, metadata)
}
```

---

## Final Pre-Submission Checklist

Before finalizing, verify all of the following:

- [ ] Every required Python class/function name exists in Scala
- [ ] Package declaration matches test/import expectations
- [ ] Scala version compatibility is 2.13-safe
- [ ] `Option` is used for nullable/defaulted behavior where appropriate
- [ ] Formatting/parsing code handles edge cases intentionally
- [ ] Top-level Python helpers are exposed via an `object`
- [ ] `scalac` compile succeeds
- [ ] `sbt compile` or `sbt test` succeeds when available
- [ ] No accidental renames or dropped constructor defaults
- [ ] Code is idiomatic Scala, not a line-for-line Python rewrite

If you follow this workflow, you will usually converge quickly on a translation that is both evaluator-safe and maintainable.