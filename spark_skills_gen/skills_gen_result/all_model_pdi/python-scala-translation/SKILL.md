---
title: "Python to Scala Code Translation"
category: "python-scala-translation"
domain: "cross-language-translation"
tags:
  - python
  - scala
  - translation
  - functional-programming
  - type-system
  - idiomatic-code
applicability:
  - Translating Python modules/classes to idiomatic Scala 2.13
  - Preserving all public API surface (classes, functions, methods)
  - Adapting Python patterns to Scala's type system and FP conventions
  - Ensuring compilation and test passage in an sbt project
---

# Python to Scala Code Translation

## Overview

This skill covers the systematic translation of Python code into idiomatic Scala 2.13. The goal is NOT a word-for-word transliteration — it is a faithful reimplementation that preserves semantics while embracing Scala's type system, immutability, pattern matching, and functional programming conventions. A proficient Scala developer reviewing the output should find it natural.

---

## High-Level Workflow

### Step 1: Analyze the Python Source Thoroughly

Read the entire Python file end-to-end. Catalog every:
- **Class** (including base classes, abstract classes, dataclasses, enums)
- **Function** (module-level and methods)
- **Type annotations** (these guide your Scala types)
- **Data structures** (dicts, lists, sets, tuples, unions, Optional)
- **Error handling** (try/except, custom exceptions, None returns)
- **External dependencies** (json, datetime, re, typing, abc)

**Why:** You need a complete inventory before writing any Scala. Missing a class or function means test failures. Understanding the Python type annotations tells you exactly what Scala types to use.

```bash
# Read the full file and count lines to gauge complexity
cat /root/SourceFile.py
wc -l /root/SourceFile.py
```

### Step 2: Inspect the Build Environment and Tests

Before writing code, check what's available:
- Scala version (2.13 vs 3.x changes syntax significantly)
- sbt version and `build.sbt` dependencies (circe, scalatest, etc.)
- Test file — this is your contract; read it carefully to understand expected API

```bash
which scala scalac sbt 2>/dev/null
scala -version 2>&1
cat /root/build.sbt
# Find and read test files
find /root -name "*Spec.scala" -o -name "*Test.scala" | head -10
cat /root/TokenizerSpec.scala  # or wherever tests live
```

**Why:** The test file defines the exact API surface you must implement. If tests import `TokenType.STRING` you must have exactly that. If tests call `tokenizer.tokenize(value)` returning `Option[Token]`, your signature must match. The build.sbt tells you which libraries are available (e.g., circe for JSON, scalatest for testing).

### Step 3: Create the Translation Mapping

Before writing code, build a mental (or written) mapping table:

| Python Construct | Scala Equivalent |
|---|---|
| `enum.Enum` / `StrEnum` | `sealed trait` + `case object` (ADT) |
| `@dataclass(frozen=True)` | `case class` (immutable by default) |
| `abc.ABC` / `abstractmethod` | `trait` with abstract `def` |
| `Optional[T]` / `None` | `Option[T]` / `None` |
| `Union[A, B, C]` | `sealed trait` or overloaded methods |
| `dict[str, Any]` | `Map[String, Any]` or typed map |
| `list[T]` | `Seq[T]` or `List[T]` |
| `tuple[A, B]` | `(A, B)` tuple |
| `isinstance(x, T)` | Pattern matching: `case x: T =>` |
| `try/except` | `Try[T]`, `Option[T]`, or `Either[E, T]` |
| `json` module | circe `Json` / `io.circe._` |
| `datetime` module | `java.time._` |
| `re` module | `scala.util.matching.Regex` |
| `**kwargs` | Named parameters with defaults, or `Map[String, Any]` |
| `yield` (generator) | `Iterator[T]` |
| `@staticmethod` | Companion object method |
| `@property` | `val` in case class, or `def` |
| `raise ValueError` | `throw new IllegalArgumentException` or return `Option`/`Either` |

### Step 4: Write the Scala File Incrementally

Build the file in sections, compiling after each major section to catch errors early. Order matters — define types before they're used.

**Recommended order:**
1. Package declaration and imports
2. ADT types (sealed traits + case objects) — e.g., `TokenType`
3. Core data classes (case classes) — e.g., `Token`
4. Abstract base traits — e.g., `BaseTokenizer`
5. Concrete implementations — e.g., `StringTokenizer`, `NumericTokenizer`
6. Composite/orchestrator classes — e.g., `UniversalTokenizer`
7. Builder patterns — e.g., `TokenizerBuilder`
8. Module-level functions — e.g., `tokenize`, `tokenizeBatch`
9. Companion objects with factory methods

### Step 5: Compile and Test

```bash
# Compile first
cd /root/tokenizer-project && sbt compile 2>&1

# Then run tests
cd /root/tokenizer-project && sbt test 2>&1
```

**Why:** Compile early and often. Scala's type system catches many errors at compile time. Fix compilation errors before running tests.

### Step 6: Verify File Placement

The task usually specifies an exact output path (e.g., `/root/Tokenizer.scala`). Make sure the file exists there, even if sbt needs it in `src/main/scala/`. You may need the file in both places, or symlink.

```bash
# Ensure the file is where the task expects it
cp /root/tokenizer-project/src/main/scala/Tokenizer.scala /root/Tokenizer.scala
```

---

## Detailed Translation Patterns

### Pattern 1: Python Enum → Scala Sealed Trait ADT

**Python:**
```python
from enum import Enum

class TokenType(str, Enum):
    STRING = "string"
    NUMERIC = "numeric"
    TEMPORAL = "temporal"
    NULL = "null"
    UNKNOWN = "unknown"
```

**Scala:**
```scala
sealed trait TokenType {
  def value: String
}

object TokenType {
  case object STRING extends TokenType { val value = "string" }
  case object NUMERIC extends TokenType { val value = "numeric" }
  case object TEMPORAL extends TokenType { val value = "temporal" }
  case object NULL extends TokenType { val value = "null" }
  case object UNKNOWN extends TokenType { val value = "unknown" }

  // If Python code uses TokenType("string") to look up by value:
  def fromString(s: String): Option[TokenType] = s match {
    case "string"   => Some(STRING)
    case "numeric"  => Some(NUMERIC)
    case "temporal" => Some(TEMPORAL)
    case "null"     => Some(NULL)
    case "unknown"  => Some(UNKNOWN)
    case _          => None
  }

  val values: Seq[TokenType] = Seq(STRING, NUMERIC, TEMPORAL, NULL, UNKNOWN)
}
```

**Key points:**
- `sealed` ensures exhaustive pattern matching
- Each variant is a `case object` (singleton, gets `equals`/`hashCode`/`toString` for free)
- The `value` field mirrors Python's `str(enum_member)` behavior
- Provide `fromString` if the Python code constructs enums from strings

### Pattern 2: Frozen Dataclass → Case Class

**Python:**
```python
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

@dataclass(frozen=True)
class Token:
    value: Any
    token_type: TokenType
    raw_value: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

**Scala:**
```scala
case class Token(
  value: Any,
  tokenType: TokenType,
  rawValue: Option[String] = None,
  metadata: Map[String, Any] = Map.empty
)
```

**Key points:**
- `case class` is immutable by default (like `frozen=True`)
- Use `camelCase` for field names (Scala convention), not `snake_case`
- `Optional[str]` → `Option[String]`
- `Dict[str, Any]` with `field(default_factory=dict)` → `Map[String, Any] = Map.empty`
- `copy()` method is auto-generated: `token.copy(metadata = token.metadata + ("key" -> "val"))`

### Pattern 3: Abstract Base Class → Trait

**Python:**
```python
from abc import ABC, abstractmethod

class BaseTokenizer(ABC):
    @abstractmethod
    def tokenize(self, value: Any) -> Optional[Token]:
        pass

    def can_tokenize(self, value: Any) -> bool:
        return self.tokenize(value) is not None
```

**Scala:**
```scala
trait BaseTokenizer {
  def tokenize(value: Any): Option[Token]

  def canTokenize(value: Any): Boolean =
    tokenize(value).isDefined
}
```

**Key points:**
- Abstract methods have no body
- Concrete methods get a default implementation
- `is not None` → `.isDefined` (or `.nonEmpty` for collections)

### Pattern 4: isinstance Dispatch → Pattern Matching

**Python:**
```python
def tokenize(self, value: Any) -> Optional[Token]:
    if value is None:
        return Token(value=None, token_type=TokenType.NULL)
    if isinstance(value, str):
        return self._string_tokenizer.tokenize(value)
    if isinstance(value, (int, float)):
        return self._numeric_tokenizer.tokenize(value)
    if isinstance(value, datetime):
        return self._temporal_tokenizer.tokenize(value)
    return Token(value=str(value), token_type=TokenType.UNKNOWN)
```

**Scala:**
```scala
def tokenize(value: Any): Option[Token] = value match {
  case null                    => Some(Token(value = None, tokenType = TokenType.NULL))
  case s: String               => stringTokenizer.tokenize(s)
  case n: Int                  => numericTokenizer.tokenize(n)
  case n: Long                 => numericTokenizer.tokenize(n)
  case n: Double               => numericTokenizer.tokenize(n)
  case n: Float                => numericTokenizer.tokenize(n)
  case n: java.math.BigDecimal => numericTokenizer.tokenize(n)
  case dt: LocalDateTime       => temporalTokenizer.tokenize(dt)
  case d: LocalDate            => temporalTokenizer.tokenize(d)
  case other                   => Some(Token(value = other.toString, tokenType = TokenType.UNKNOWN))
}
```

**Key points:**
- Scala pattern matching replaces chains of `isinstance`
- Be explicit about numeric types — Python's `int`/`float` map to multiple JVM types
- `None` in Python (null value) maps to Scala `null` at the boundary, but wrap in `Option` internally
- Order matters: more specific types before general ones

### Pattern 5: Generator / yield → Iterator

**Python:**
```python
def tokenize_batch(values: list[Any], tokenizer: BaseTokenizer) -> Iterator[Optional[Token]]:
    for value in values:
        yield tokenizer.tokenize(value)
```

**Scala:**
```scala
def tokenizeBatch(values: Seq[Any], tokenizer: BaseTokenizer): Iterator[Option[Token]] =
  values.iterator.map(tokenizer.tokenize)
```

### Pattern 6: Builder Pattern (Immutable)

**Python:**
```python
class TokenizerBuilder:
    def __init__(self):
        self._tokenizers = []
        self._metadata = {}

    def with_tokenizer(self, tokenizer):
        self._tokenizers.append(tokenizer)
        return self

    def with_metadata(self, key, value):
        self._metadata[key] = value
        return self

    def build(self):
        return UniversalTokenizer(self._tokenizers, self._metadata)
```

**Scala (immutable builder):**
```scala
case class TokenizerBuilder(
  private val tokenizers: Seq[BaseTokenizer] = Seq.empty,
  private val metadata: Map[String, Any] = Map.empty
) {
  def withTokenizer(tokenizer: BaseTokenizer): TokenizerBuilder =
    copy(tokenizers = tokenizers :+ tokenizer)

  def withMetadata(key: String, value: Any): TokenizerBuilder =
    copy(metadata = metadata + (key -> value))

  def build(): UniversalTokenizer =
    UniversalTokenizer(tokenizers, metadata)
}
```

**Key points:**
- Use `case class` + `copy` for immutable builder — each method returns a new instance
- This is idiomatic Scala; mutable builders are a code smell
- `:+` appends to a `Seq`; `+` adds to a `Map`

### Pattern 7: Python dict / JSON → Circe Json

**Python:**
```python
import json

class JsonTokenizer:
    def tokenize(self, value):
        if isinstance(value, str):
            parsed = json.loads(value)
            return Token(value=parsed, token_type=TokenType.STRING)
```

**Scala (with circe):**
```scala
import io.circe._
import io.circe.parser._

class JsonTokenizer extends BaseTokenizer {
  def tokenize(value: Any): Option[Token] = value match {
    case s: String =>
      parse(s).toOption.map { json =>
        Token(value = json, tokenType = TokenType.STRING)
      }
    case j: Json =>
      Some(Token(value = j, tokenType = TokenType.STRING))
    case _ => None
  }
}
```

### Pattern 8: Python datetime → java.time

**Python:**
```python
from datetime import datetime, date

class TemporalTokenizer:
    FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"]

    def tokenize(self, value):
        if isinstance(value, datetime):
            return Token(value=value.isoformat(), token_type=TokenType.TEMPORAL)
        if isinstance(value, str):
            for fmt in self.FORMATS:
                try:
                    parsed = datetime.strptime(value, fmt)
                    return Token(value=parsed.isoformat(), token_type=TokenType.TEMPORAL)
                except ValueError:
                    continue
        return None
```

**Scala:**
```scala
import java.time._
import java.time.format.DateTimeFormatter
import scala.util.Try

class TemporalTokenizer extends BaseTokenizer {
  private val formats: Seq[DateTimeFormatter] = Seq(
    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"),
    DateTimeFormatter.ofPattern("yyyy-MM-dd"),
    DateTimeFormatter.ofPattern("MM/dd/yyyy")
  )

  def tokenize(value: Any): Option[Token] = value match {
    case dt: LocalDateTime =>
      Some(Token(value = dt.toString, tokenType = TokenType.TEMPORAL, rawValue = Some(dt.toString)))
    case d: LocalDate =>
      Some(Token(value = d.toString, tokenType = TokenType.TEMPORAL, rawValue = Some(d.toString)))
    case s: String =>
      formats.collectFirst {
        case fmt if Try(LocalDateTime.parse(s, fmt)).isSuccess =>
          Token(value = LocalDateTime.parse(s, fmt).toString, tokenType = TokenType.TEMPORAL, rawValue = Some(s))
        case fmt if Try(LocalDate.parse(s, fmt)).isSuccess =>
          Token(value = LocalDate.parse(s, fmt).toString, tokenType = TokenType.TEMPORAL, rawValue = Some(s))
      }
    case _ => None
  }
}
```

**Key points:**
- Python `strptime` format codes differ from Java's `DateTimeFormatter` patterns: `%Y` → `yyyy`, `%m` → `MM`, `%d` → `dd`, `%H` → `HH`, `%M` → `mm`, `%S` → `ss`
- Use `Try(expr).isSuccess` for safe parsing attempts
- `collectFirst` with pattern guards replaces the Python try/except loop elegantly

### Pattern 9: Naming Conventions

| Python | Scala |
|---|---|
| `snake_case` functions/methods | `camelCase` |
| `snake_case` variables | `camelCase` |
| `PascalCase` classes | `PascalCase` |
| `UPPER_SNAKE` constants | `PascalCase` or `UPPER_SNAKE` in companion objects |
| `_private` prefix | `private` keyword |
| `__dunder__` methods | Override standard Scala methods (`toString`, `equals`) |

### Pattern 10: Error Handling

| Python | Scala |
|---|---|
| `try/except ValueError` | `Try[T]` or `Option[T]` |
| `raise ValueError("msg")` | `throw new IllegalArgumentException("msg")` or return `Left("msg")` |
| Return `None` on failure | Return `Option.empty[T]` / `None` |
| `assert condition` | `require(condition, "msg")` (throws `IllegalArgumentException`) |

Prefer `Option`/`Try`/`Either` over throwing exceptions in Scala. Only throw when the Python code raises exceptions that callers are expected to catch.

---

## Handling Variance and Generics

When the Python code uses generic containers with type parameters:

```python
class TokenContainer(Generic[T]):
    def __init__(self, items: list[T]):
        self._items = items

    def get_items(self) -> list[T]:
        return self._items
```

```scala
// Use covariance (+A) when the container only produces A values
case class TokenContainer[+A](items: Seq[A]) {
  def getItems: Seq[A] = items
  def map[B](f: A => B): TokenContainer[B] = TokenContainer(items.map(f))
}

// Use contravariance (-A) when the container only consumes A values
trait TokenSink[-A] {
  def accept(value: A): Unit
}
```

---

## Handling Companion Objects and Module-Level Functions

Python module-level functions become methods on a companion object or a package object:

```python
# Module-level functions
def tokenize(value: Any) -> Optional[Token]:
    return UniversalTokenizer().tokenize(value)

def tokenize_batch(values: list, tokenizer=None) -> Iterator:
    t = tokenizer or UniversalTokenizer()
    for v in values:
        yield t.tokenize(v)
```

```scala
// In a package object or a standalone object
object Tokenizer {
  def tokenize(value: Any): Option[Token] =
    new UniversalTokenizer().tokenize(value)

  def tokenizeBatch(values: Seq[Any], tokenizer: BaseTokenizer = new UniversalTokenizer()): Iterator[Option[Token]] =
    values.iterator.map(tokenizer.tokenize)
}
```

---

## Common Pitfalls

### 1. Missing Numeric Type Coverage
Python's `int` maps to `Int`, `Long`, `BigInt` on the JVM. Python's `float` maps to `Float`, `Double`, `BigDecimal`. If you only match `case n: Int =>` you'll miss `Long` values from tests. Always match all numeric types.

### 2. Forgetting `Option` Wrapping
Python returns `None` (a value) while Scala uses `Option[T]`. Every Python method returning `Optional[T]` must return `Option[T]` in Scala. Don't return `null` — wrap in `Some()` or return `None` (Scala's `Option.None`).

### 3. Date Format String Differences
Python `strftime`/`strptime` uses `%Y-%m-%d`. Java/Scala uses `yyyy-MM-dd`. This is a frequent source of runtime errors. Double-check every format string.

### 4. Mutable vs Immutable Builder
Python builders typically mutate `self` and `return self`. In Scala, use `case class` + `copy()` to return new instances. Mutable builders work but are not idiomatic.

### 5. snake_case Leaking Into Scala
Test files will use `camelCase` names. If you keep Python's `snake_case` in your Scala code, tests won't compile. Rename everything: `token_type` → `tokenType`, `raw_value` → `rawValue`, `can_tokenize` → `canTokenize`.

### 6. Sealed Trait Completeness
If the test references `TokenType.STRING`, your sealed trait must have exactly `case object STRING`. Check every enum variant against the test file.

### 7. Import Conflicts
`scala.None` (Option's None) vs your custom `None` — avoid naming conflicts. If your ADT has a `NULL` variant, don't name it `None`.

### 8. Forgetting to Handle `null` Input
Even in Scala, `Any` parameters can receive `null` from Java interop or tests. Add a `case null =>` branch in pattern matches that handle `Any`.

### 9. File Placement
sbt expects sources in `src/main/scala/`. The task may expect the file at `/root/Tokenizer.scala`. Ensure both locations have the file (copy or symlink).

### 10. Not Reading the Test File
The test file is your specification. Read it before writing any code. It tells you exact method signatures, return types, class names, and expected behaviors.

---

## Reference Implementation

This is a complete, self-contained Scala file that translates a typical Python tokenizer module. Adapt class names, methods, and logic to match your specific Python source.

```scala
// Tokenizer.scala
// Complete Python-to-Scala translation of a tokenizer module
// Targets Scala 2.13, uses circe for JSON, java.time for temporal

import io.circe._
import io.circe.parser._
import java.time._
import java.time.format.DateTimeFormatter
import scala.util.Try

// ============================================================
// 1. TokenType ADT (replaces Python Enum)
// ============================================================
sealed trait TokenType {
  def value: String
  override def toString: String = value
}

object TokenType {
  case object STRING   extends TokenType { val value = "string" }
  case object NUMERIC  extends TokenType { val value = "numeric" }
  case object TEMPORAL extends TokenType { val value = "temporal" }
  case object NULL     extends TokenType { val value = "null" }
  case object UNKNOWN  extends TokenType { val value = "unknown" }
  case object JSON     extends TokenType { val value = "json" }

  val values: Seq[TokenType] = Seq(STRING, NUMERIC, TEMPORAL, NULL, UNKNOWN, JSON)

  def fromString(s: String): Option[TokenType] = values.find(_.value == s)
}

// ============================================================
// 2. Token case class (replaces frozen dataclass)
// ============================================================
case class Token(
  value: Any,
  tokenType: TokenType,
  rawValue: Option[String] = None,
  metadata: Map[String, Any] = Map.empty
) {
  /** Returns a new Token with additional metadata (immutable) */
  def withMetadata(key: String, metaValue: Any): Token =
    copy(metadata = metadata + (key -> metaValue))
}

// Helper for creating tokens with metadata in one call
object Token {
  def apply(value: Any, tokenType: TokenType, rawValue: Option[String], meta: (String, Any)*): Token =
    new Token(value, tokenType, rawValue, meta.toMap)
}

// ============================================================
// 3. Generic TokenContainer (replaces Generic[T] container)
// ============================================================
case class TokenContainer[+A](items: Seq[A]) {
  def getItems: Seq[A] = items
  def map[B](f: A => B): TokenContainer[B] = TokenContainer(items.map(f))
  def flatMap[B](f: A => TokenContainer[B]): TokenContainer[B] =
    TokenContainer(items.flatMap(a => f(a).items))
  def filter(p: A => Boolean): TokenContainer[A] = TokenContainer(items.filter(p))
  def size: Int = items.size
  def isEmpty: Boolean = items.isEmpty
  def nonEmpty: Boolean = items.nonEmpty
  def head: A = items.head
  def headOption: Option[A] = items.headOption
}

object TokenContainer {
  def empty[A]: TokenContainer[A] = TokenContainer(Seq.empty[A])
  def single[A](item: A): TokenContainer[A] = TokenContainer(Seq(item))
}

// ============================================================
// 4. TokenFunctor (type class for mapping over token structures)
// ============================================================
trait TokenFunctor[F[_]] {
  def map[A, B](fa: F[A])(f: A => B): F[B]
}

object TokenFunctor {
  implicit val containerFunctor: TokenFunctor[TokenContainer] = new TokenFunctor[TokenContainer] {
    def map[A, B](fa: TokenContainer[A])(f: A => B): TokenContainer[B] = fa.map(f)
  }

  // Syntax enrichment for .map via functor
  implicit class FunctorOps[F[_], A](fa: F[A])(implicit F: TokenFunctor[F]) {
    def fmap[B](f: A => B): F[B] = F.map(fa)(f)
  }
}

// ============================================================
// 5. BaseTokenizer trait (replaces ABC)
// ============================================================
trait BaseTokenizer {
  def tokenize(value: Any): Option[Token]

  def canTokenize(value: Any): Boolean = tokenize(value).isDefined
}

// ============================================================
// 6. StringTokenizer
// ============================================================
class StringTokenizer extends BaseTokenizer {
  def tokenize(value: Any): Option[Token] = value match {
    case s: String if s.nonEmpty =>
      Some(Token(
        value = s.trim,
        tokenType = TokenType.STRING,
        rawValue = Some(s)
      ))
    case s: String =>
      Some(Token(
        value = s,
        tokenType = TokenType.STRING,
        rawValue = Some(s)
      ))
    case _ => None
  }
}

// ============================================================
// 7. NumericTokenizer
// ============================================================
class NumericTokenizer extends BaseTokenizer {
  def tokenize(value: Any): Option[Token] = value match {
    case n: Int =>
      Some(Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: Long =>
      Some(Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: Float =>
      Some(Token(value = n.toDouble, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: Double =>
      Some(Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: java.math.BigDecimal =>
      Some(Token(value = BigDecimal(n), tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: BigDecimal =>
      Some(Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case n: BigInt =>
      Some(Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(n.toString)))
    case s: String =>
      parseNumericString(s)
    case _ => None
  }

  private def parseNumericString(s: String): Option[Token] = {
    val trimmed = s.trim
    if (trimmed.isEmpty) return None

    // Try integer first, then double
    Try(trimmed.toLong).map { n =>
      Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(s))
    }.orElse {
      Try(trimmed.toDouble).map { n =>
        Token(value = n, tokenType = TokenType.NUMERIC, rawValue = Some(s))
      }
    }.toOption
  }
}

// ============================================================
// 8. TemporalTokenizer
// ============================================================
class TemporalTokenizer extends BaseTokenizer {
  private val dateTimeFormats: Seq[DateTimeFormatter] = Seq(
    DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"),
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"),
    DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS"),
    DateTimeFormatter.ofPattern("MM/dd/yyyy HH:mm:ss")
  )

  private val dateFormats: Seq[DateTimeFormatter] = Seq(
    DateTimeFormatter.ofPattern("yyyy-MM-dd"),
    DateTimeFormatter.ofPattern("MM/dd/yyyy"),
    DateTimeFormatter.ofPattern("dd/MM/yyyy")
  )

  def tokenize(value: Any): Option[Token] = value match {
    case dt: LocalDateTime =>
      Some(Token(value = dt.toString, tokenType = TokenType.TEMPORAL, rawValue = Some(dt.toString)))
    case d: LocalDate =>
      Some(Token(value = d.toString, tokenType = TokenType.TEMPORAL, rawValue = Some(d.toString)))
    case s: String =>
      tryParseDateTime(s).orElse(tryParseDate(s))
    case _ => None
  }

  private def tryParseDateTime(s: String): Option[Token] =
    dateTimeFormats.collectFirst {
      case fmt if Try(LocalDateTime.parse(s, fmt)).isSuccess =>
        Token(
          value = LocalDateTime.parse(s, fmt).toString,
          tokenType = TokenType.TEMPORAL,
          rawValue = Some(s)
        )
    }

  private def tryParseDate(s: String): Option[Token] =
    dateFormats.collectFirst {
      case fmt if Try(LocalDate.parse(s, fmt)).isSuccess =>
        Token(
          value = LocalDate.parse(s, fmt).toString,
          tokenType = TokenType.TEMPORAL,
          rawValue = Some(s)
        )
    }
}

// ============================================================
// 9. JsonTokenizer (uses circe)
// ============================================================
class JsonTokenizer extends BaseTokenizer {
  def tokenize(value: Any): Option[Token] = value match {
    case s: String =>
      parse(s).toOption.map { json =>
        Token(value = json, tokenType = TokenType.JSON, rawValue = Some(s))
      }
    case j: Json =>
      Some(Token(value = j, tokenType = TokenType.JSON, rawValue = Some(j.noSpaces)))
    case _ => None
  }
}

// ============================================================
// 10. WhitespaceTokenizer (handles whitespace normalization)
// ============================================================
class WhitespaceTokenizer(
  trim: Boolean = true,
  collapseSpaces: Boolean = false,
  removeAll: Boolean = false
) extends BaseTokenizer {

  def tokenize(value: Any): Option[Token] = value match {
    case s: String =>
      var result = s
      if (removeAll) result = result.replaceAll("\\s+", "")
      else {
        if (trim) result = result.trim
        if (collapseSpaces) result = result.replaceAll("\\s+", " ")
      }
      Some(Token(value = result, tokenType = TokenType.STRING, rawValue = Some(s)))
    case _ => None
  }

  /** Combine with another WhitespaceTokenizer's options */
  def combine(other: WhitespaceTokenizer): WhitespaceTokenizer =
    new WhitespaceTokenizer(
      trim = this.trim || other.trim,