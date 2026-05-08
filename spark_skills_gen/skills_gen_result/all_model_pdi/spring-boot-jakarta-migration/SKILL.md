---
title: "Spring Boot 2.7 → 3.2 / Java 8 → 21 / Jakarta Migration"
category: spring-boot-jakarta-migration
tags:
  - spring-boot
  - jakarta-ee
  - java-21
  - spring-security-6
  - hibernate-6
  - restclient
  - migration
applicability: >
  Any Spring Boot microservice migrating from 2.x (Java 8/11) to 3.x (Java 17+).
  Covers namespace migration, security rewrite, RestTemplate→RestClient, JJWT upgrade,
  Hibernate 6 property changes, and POM dependency updates.
---

# Spring Boot 2.7 → 3.2 & Jakarta EE Migration

## 1. High-Level Workflow

| # | Step | Why | Key Decision |
|---|------|-----|--------------|
| 1 | **Inventory the codebase** | Understand every file that imports `javax.*`, uses deprecated Spring Security APIs, or calls `RestTemplate`. | Use `grep -rn` to build a hit list before touching anything. |
| 2 | **Update `pom.xml`** | Spring Boot 3.2 requires Java 17+ and pulls Jakarta EE 10 transitively. JJWT 0.9.x is incompatible. | Bump parent, Java version, and replace JJWT monolith with modular artifacts. Remove `javax.xml.bind:jaxb-api`. |
| 3 | **Migrate `javax.*` → `jakarta.*`** | Spring Boot 3 / Hibernate 6 / Tomcat 10 all use Jakarta namespace. | Mechanical find-and-replace across every `.java` file. |
| 4 | **Rewrite Spring Security config** | `WebSecurityConfigurerAdapter` is removed in Security 6. Method-security annotation changed. | Convert to `SecurityFilterChain` bean with lambda DSL. |
| 5 | **Migrate `RestTemplate` → `RestClient`** | `RestTemplate` is in maintenance mode; `RestClient` is the idiomatic Spring 6 replacement. | Fluent builder API; no `exchange()` / `ResponseEntity` gymnastics. |
| 6 | **Update JJWT usage** | 0.9.x → 0.12.x has breaking API changes (`Jwts.builder()`, `Jwts.parser()`, key handling). | Use `Jwts.parser().verifyWith(key).build().parseSignedClaims(token)`. |
| 7 | **Fix Hibernate 6 / JPA properties** | Some `spring.jpa.properties.hibernate.*` keys changed. `javax.persistence.*` properties are gone. | Audit `application.properties` / `application.yml`. |
| 8 | **Compile & test** | Catch remaining issues. | `mvn clean compile` then `mvn test`. Fix iteratively. |

---

## 2. Environment Setup

The migration environment typically provides SDKMAN for Java version management:

```bash
# Activate Java 21 (adjust version to what's installed)
source /root/.sdkman/bin/sdkman-init.sh
sdk use java 21.0.2-tem

# Verify
java -version   # → openjdk 21.0.2
mvn -version    # → Apache Maven 3.x, Java 21
```

---

## 3. Step-by-Step with Code

### 3.1 Inventory the Codebase

```bash
# Find all Java, XML, and properties files
find /workspace -type f \( -name "*.java" -o -name "*.xml" -o -name "*.properties" -o -name "*.yml" \)

# List every javax import that needs migration
grep -rn "import javax\." /workspace/src --include="*.java"

# Find deprecated Security patterns
grep -rn "WebSecurityConfigurerAdapter\|antMatchers\|authorizeRequests\|EnableGlobalMethodSecurity" \
  /workspace/src --include="*.java"

# Find RestTemplate usage
grep -rn "RestTemplate" /workspace/src --include="*.java"

# Find JJWT usage
grep -rn "Jwts\.\|io\.jsonwebtoken" /workspace/src --include="*.java"
```

### 3.2 Update `pom.xml`

Key changes to make in the POM:

```xml
<!-- BEFORE -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>2.7.18</version>
</parent>
<properties>
    <java.version>1.8</java.version>
</properties>

<!-- AFTER -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
</parent>
<properties>
    <java.version>21</java.version>
</properties>
```

JJWT dependency replacement:

```xml
<!-- REMOVE this single dependency -->
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt</artifactId>
    <version>0.9.1</version>
</dependency>

<!-- REMOVE javax.xml.bind (no longer needed with JJWT 0.12+) -->
<dependency>
    <groupId>javax.xml.bind</groupId>
    <artifactId>jaxb-api</artifactId>
    <version>2.3.1</version>
</dependency>

<!-- ADD these three modular JJWT dependencies -->
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-api</artifactId>
    <version>0.12.6</version>
</dependency>
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-impl</artifactId>
    <version>0.12.6</version>
    <scope>runtime</scope>
</dependency>
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt-jackson</artifactId>
    <version>0.12.6</version>
    <scope>runtime</scope>
</dependency>
```

### 3.3 Namespace Migration: `javax.*` → `jakarta.*`

This is a mechanical replacement. The three namespaces that matter:

| Old | New |
|-----|-----|
| `javax.persistence.*` | `jakarta.persistence.*` |
| `javax.validation.*` | `jakarta.validation.*` |
| `javax.servlet.*` | `jakarta.servlet.*` |

```bash
# Bulk replace across all Java files
find /workspace/src -name "*.java" -exec sed -i \
  -e 's/import javax\.persistence/import jakarta.persistence/g' \
  -e 's/import javax\.validation/import jakarta.validation/g' \
  -e 's/import javax\.servlet/import jakarta.servlet/g' \
  {} +

# Verify no javax references remain (should return empty)
grep -rn "import javax\." /workspace/src --include="*.java"
```

**Important:** Do NOT replace `javax.crypto.*` or `javax.net.*` — those are part of the JDK, not Jakarta EE.

Example — a JPA entity before and after:

```java
// BEFORE
import javax.persistence.*;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Email;

// AFTER
import jakarta.persistence.*;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Email;
```

### 3.4 Spring Security 6 Migration

This is the most complex single-file rewrite. The old pattern using `WebSecurityConfigurerAdapter` is completely removed.

```java
// ============================================================
// BEFORE — Spring Security 5 / Spring Boot 2.7
// ============================================================
import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;
import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity;

@Configuration
@EnableWebSecurity
@EnableGlobalMethodSecurity(prePostEnabled = true)
public class SecurityConfig extends WebSecurityConfigurerAdapter {

    @Autowired private JwtAuthenticationFilter jwtFilter;
    @Autowired private UserDetailsService userDetailsService;

    @Override
    protected void configure(HttpSecurity http) throws Exception {
        http.csrf().disable()
            .authorizeRequests()
                .antMatchers("/api/auth/**").permitAll()
                .antMatchers("/api/admin/**").hasRole("ADMIN")
                .anyRequest().authenticated()
            .and()
            .sessionManagement()
                .sessionCreationPolicy(SessionCreationPolicy.STATELESS);

        http.addFilterBefore(jwtFilter, UsernamePasswordAuthenticationFilter.class);
    }

    @Override
    protected void configure(AuthenticationManagerBuilder auth) throws Exception {
        auth.userDetailsService(userDetailsService).passwordEncoder(passwordEncoder());
    }

    @Bean
    @Override
    public AuthenticationManager authenticationManagerBean() throws Exception {
        return super.authenticationManagerBean();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
```

```java
// ============================================================
// AFTER — Spring Security 6 / Spring Boot 3.2
// ============================================================
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableWebSecurity
@EnableMethodSecurity  // replaces @EnableGlobalMethodSecurity(prePostEnabled = true)
public class SecurityConfig {
    // NO extends WebSecurityConfigurerAdapter

    private final JwtAuthenticationFilter jwtAuthenticationFilter;

    public SecurityConfig(JwtAuthenticationFilter jwtAuthenticationFilter) {
        this.jwtAuthenticationFilter = jwtAuthenticationFilter;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
            .csrf(csrf -> csrf.disable())
            .authorizeHttpRequests(auth -> auth          // NOT authorizeRequests
                .requestMatchers("/api/auth/**").permitAll()  // NOT antMatchers
                .requestMatchers("/api/admin/**").hasRole("ADMIN")
                .anyRequest().authenticated()
            )
            .sessionManagement(session ->
                session.sessionCreationPolicy(SessionCreationPolicy.STATELESS)
            );

        http.addFilterBefore(jwtAuthenticationFilter,
                             UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    @Bean
    public AuthenticationManager authenticationManager(
            AuthenticationConfiguration authConfig) throws Exception {
        return authConfig.getAuthenticationManager();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
```

**Cheat sheet of renames:**

| Spring Security 5 | Spring Security 6 |
|---|---|
| `extends WebSecurityConfigurerAdapter` | Remove — use `@Bean SecurityFilterChain` |
| `@EnableGlobalMethodSecurity(prePostEnabled = true)` | `@EnableMethodSecurity` |
| `.authorizeRequests()` | `.authorizeHttpRequests()` |
| `.antMatchers(...)` | `.requestMatchers(...)` |
| `authenticationManagerBean()` | Inject `AuthenticationConfiguration` |
| `.csrf().disable()` | `.csrf(csrf -> csrf.disable())` (lambda DSL) |
| `.sessionManagement().sessionCreationPolicy(...)` | `.sessionManagement(s -> s.sessionCreationPolicy(...))` |

### 3.5 RestTemplate → RestClient Migration

```java
// ============================================================
// BEFORE — RestTemplate
// ============================================================
import org.springframework.web.client.RestTemplate;
import org.springframework.http.ResponseEntity;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;

@Service
public class ExternalApiService {

    private final RestTemplate restTemplate;

    public ExternalApiService(RestTemplateBuilder builder) {
        this.restTemplate = builder.rootUri("https://api.example.com").build();
    }

    public UserDTO getUser(Long id) {
        return restTemplate.getForObject("/users/{id}", UserDTO.class, id);
    }

    public UserDTO createUser(CreateUserRequest request) {
        return restTemplate.postForObject("/users", request, UserDTO.class);
    }

    public void deleteUser(Long id) {
        restTemplate.delete("/users/{id}", id);
    }

    public List<UserDTO> getAllUsers() {
        ResponseEntity<List<UserDTO>> response = restTemplate.exchange(
            "/users", HttpMethod.GET, null,
            new ParameterizedTypeReference<List<UserDTO>>() {});
        return response.getBody();
    }
}
```

```java
// ============================================================
// AFTER — RestClient (Spring 6.1+ / Spring Boot 3.2+)
// ============================================================
import org.springframework.web.client.RestClient;
import org.springframework.core.ParameterizedTypeReference;

@Service
public class ExternalApiService {

    private final RestClient restClient;

    public ExternalApiService(RestClient.Builder builder) {
        this.restClient = builder.baseUrl("https://api.example.com").build();
    }

    public UserDTO getUser(Long id) {
        return restClient.get()
                .uri("/users/{id}", id)
                .retrieve()
                .body(UserDTO.class);
    }

    public UserDTO createUser(CreateUserRequest request) {
        return restClient.post()
                .uri("/users")
                .body(request)
                .retrieve()
                .body(UserDTO.class);
    }

    public void deleteUser(Long id) {
        restClient.delete()
                .uri("/users/{id}", id)
                .retrieve()
                .toBodilessEntity();
    }

    public List<UserDTO> getAllUsers() {
        return restClient.get()
                .uri("/users")
                .retrieve()
                .body(new ParameterizedTypeReference<List<UserDTO>>() {});
    }
}
```

**Key differences:**
- `RestClient.Builder` is auto-configured by Spring Boot 3.2 (inject it directly).
- `.rootUri()` → `.baseUrl()`.
- Fluent chain: `.get()` / `.post()` / `.delete()` → `.uri()` → `.retrieve()` → `.body()`.
- For void responses use `.toBodilessEntity()`.
- For generic types use `.body(new ParameterizedTypeReference<...>() {})`.

### 3.6 JJWT 0.9.x → 0.12.x API Migration

```java
// ============================================================
// BEFORE — JJWT 0.9.x
// ============================================================
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;

// Generating a token
String token = Jwts.builder()
        .setSubject(username)
        .setIssuedAt(new Date())
        .setExpiration(new Date(System.currentTimeMillis() + expiration))
        .signWith(SignatureAlgorithm.HS512, secretKeyString)
        .compact();

// Parsing a token
Claims claims = Jwts.parser()
        .setSigningKey(secretKeyString)
        .parseClaimsJws(token)
        .getBody();

String username = claims.getSubject();
```

```java
// ============================================================
// AFTER — JJWT 0.12.x
// ============================================================
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;

// Build a proper SecretKey from the string
private SecretKey getSigningKey() {
    return Keys.hmacShaKeyFor(secretKeyString.getBytes(StandardCharsets.UTF_8));
}

// Generating a token
String token = Jwts.builder()
        .subject(username)                    // NOT setSubject
        .issuedAt(new Date())                 // NOT setIssuedAt
        .expiration(new Date(System.currentTimeMillis() + expiration))  // NOT setExpiration
        .signWith(getSigningKey())            // Key object, algorithm inferred
        .compact();

// Parsing a token
Claims claims = Jwts.parser()
        .verifyWith(getSigningKey())          // NOT setSigningKey
        .build()                              // NEW — must call build()
        .parseSignedClaims(token)             // NOT parseClaimsJws
        .getPayload();                        // NOT getBody

String username = claims.getSubject();
```

**JJWT rename cheat sheet:**

| 0.9.x | 0.12.x |
|-------|--------|
| `.setSubject()` | `.subject()` |
| `.setIssuedAt()` | `.issuedAt()` |
| `.setExpiration()` | `.expiration()` |
| `.signWith(SignatureAlgorithm.HS512, stringKey)` | `.signWith(secretKeyObject)` |
| `Jwts.parser().setSigningKey(key)` | `Jwts.parser().verifyWith(key).build()` |
| `.parseClaimsJws(token)` | `.parseSignedClaims(token)` |
| `.getBody()` | `.getPayload()` |

**Critical:** The secret key string must be long enough for the algorithm. HS256 needs ≥ 32 bytes, HS384 ≥ 48 bytes, HS512 ≥ 64 bytes. If the existing key is too short, pad it or switch to HS256.

### 3.7 Hibernate 6 / JPA Property Updates

Check `application.properties` or `application.yml`:

```properties
# These are fine — no change needed
spring.jpa.hibernate.ddl-auto=update
spring.jpa.show-sql=true

# CHANGE if present:
# BEFORE (Hibernate 5)
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.MySQL8Dialect
# AFTER (Hibernate 6 — dialect is auto-detected, but if explicit:)
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.MySQLDialect

# H2 dialect change:
# BEFORE
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.H2Dialect
# AFTER (Hibernate 6 auto-detects; remove the line or use:)
# spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.H2Dialect
# (H2Dialect still exists in Hibernate 6, but many versioned dialects were removed)
```

**Hibernate 6 key changes:**
- Many versioned dialect classes removed (e.g., `MySQL8Dialect` → `MySQLDialect`).
- `javax.persistence.*` properties in config → `jakarta.persistence.*`.
- ID generation strategy defaults changed — `GenerationType.AUTO` now uses sequences by default instead of identity. If your DB doesn't support sequences (MySQL), explicitly use `GenerationType.IDENTITY`.

### 3.8 Compile and Test

```bash
source /root/.sdkman/bin/sdkman-init.sh
sdk use java 21.0.2-tem

cd /workspace

# Step 1: Compile — fix all errors before moving to tests
mvn clean compile 2>&1 | tail -50

# Step 2: Run tests
mvn test 2>&1 | tail -80
```

Common compile errors and fixes:
- `cannot find symbol: class WebSecurityConfigurerAdapter` → You missed the Security rewrite.
- `package javax.persistence does not exist` → Incomplete namespace migration.
- `cannot find symbol: method setSubject(String)` → JJWT API not updated.
- `cannot find symbol: class RestTemplate` → RestTemplate import still present after migration.

---

## 4. Common Pitfalls

### Pitfall 1: Incomplete `javax` → `jakarta` replacement
**Symptom:** `package javax.persistence does not exist` at compile time.
**Cause:** Missed some files, or replaced only `persistence` but not `validation` or `servlet`.
**Fix:** Always grep for ALL `javax.` imports after replacement. Use the `find ... -exec sed` command to hit every file.

### Pitfall 2: Forgetting `.build()` on JJWT parser
**Symptom:** `Jwts.parser().verifyWith(key).parseSignedClaims(token)` won't compile.
**Cause:** JJWT 0.12 uses a builder pattern — `parser()` returns a builder, not a parser.
**Fix:** Always chain `.build()` before `.parseSignedClaims()`.

### Pitfall 3: Using `antMatchers` instead of `requestMatchers`
**Symptom:** `cannot find symbol: method antMatchers(String)`.
**Cause:** `antMatchers` was removed in Spring Security 6.
**Fix:** Replace with `requestMatchers`. The method signature is identical.

### Pitfall 4: Mixing old and new Security DSL
**Symptom:** Compile errors or runtime `IllegalStateException` about duplicate configuration.
**Cause:** Partially migrated — e.g., using lambda DSL for `csrf` but chaining style for `authorizeRequests`.
**Fix:** Use lambda DSL consistently for ALL security configuration methods.

### Pitfall 5: Keeping `javax.xml.bind:jaxb-api` dependency
**Symptom:** Classpath conflicts or unnecessary dependency warnings.
**Cause:** JJWT 0.9.x needed JAXB for Base64 decoding; 0.12.x does not.
**Fix:** Remove the `jaxb-api` dependency from `pom.xml`.

### Pitfall 6: `RestClient.Builder` not found
**Symptom:** `No qualifying bean of type 'RestClient.Builder'`.
**Cause:** Spring Boot version is below 3.2, or `spring-boot-starter-web` is missing.
**Fix:** Ensure Spring Boot 3.2.0+ and `spring-boot-starter-web` is in dependencies.

### Pitfall 7: Replacing `javax.crypto` or `javax.net` imports
**Symptom:** `package jakarta.crypto does not exist`.
**Cause:** Over-zealous find-and-replace. `javax.crypto` and `javax.net` are JDK packages, NOT Jakarta EE.
**Fix:** Only replace `javax.persistence`, `javax.validation`, and `javax.servlet`.

### Pitfall 8: JJWT secret key too short
**Symptom:** `WeakKeyException: The signing key's size is X bits which is not secure enough for the HS512 algorithm.`
**Cause:** JJWT 0.12 enforces minimum key lengths. HS512 requires ≥ 512 bits (64 bytes).
**Fix:** Use a longer secret or switch to HS256 (≥ 256 bits / 32 bytes).

---

## 5. Verification Checklist

Run these checks before declaring the migration complete:

```bash
# 1. No javax EE imports remain
grep -rn "import javax\.\(persistence\|validation\|servlet\)" /workspace/src --include="*.java"
# Expected: no output

# 2. Jakarta imports present
grep -rn "import jakarta\." /workspace/src --include="*.java"
# Expected: hits in entity, DTO, filter files

# 3. No WebSecurityConfigurerAdapter
grep -rn "WebSecurityConfigurerAdapter" /workspace/src --include="*.java"
# Expected: no output

# 4. EnableMethodSecurity present (not EnableGlobalMethodSecurity)
grep -rn "EnableMethodSecurity\|EnableGlobalMethodSecurity" /workspace/src --include="*.java"
# Expected: only EnableMethodSecurity

# 5. requestMatchers used (not antMatchers)
grep -rn "requestMatchers\|antMatchers" /workspace/src --include="*.java"
# Expected: only requestMatchers

# 6. RestClient used (not RestTemplate)
grep -rn "RestClient\|RestTemplate" /workspace/src --include="*.java"
# Expected: only RestClient

# 7. Clean compile
mvn clean compile

# 8. All tests pass
mvn test
```

---

## 6. Reference Implementation

This is a complete, self-contained migration script. Given a Spring Boot 2.7 project at `/workspace`, it performs the entire migration. An agent can adapt this directly.

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/workspace"
cd "$PROJECT_DIR"

# ============================================================
# 0. Environment — ensure Java 21 is active
# ============================================================
if [ -f /root/.sdkman/bin/sdkman-init.sh ]; then
    source /root/.sdkman/bin/sdkman-init.sh
    sdk use java 21.0.2-tem 2>/dev/null || true
fi
echo "Java version: $(java -version 2>&1 | head -1)"

# ============================================================
# 1. Inventory — understand what needs to change
# ============================================================
echo "=== Inventory ==="
echo "javax imports:"
grep -rn "import javax\.\(persistence\|validation\|servlet\)" src --include="*.java" || echo "  (none)"
echo "Security patterns:"
grep -rn "WebSecurityConfigurerAdapter\|antMatchers\|authorizeRequests\|EnableGlobalMethodSecurity" src --include="*.java" || echo "  (none)"
echo "RestTemplate usage:"
grep -rn "RestTemplate" src --include="*.java" || echo "  (none)"
echo "JJWT usage:"
grep -rn "io\.jsonwebtoken\|Jwts\." src --include="*.java" || echo "  (none)"

# ============================================================
# 2. Update pom.xml
# ============================================================
echo "=== Updating pom.xml ==="

# Update Spring Boot parent version
sed -i 's|<version>2\.7\.[0-9]*</version>\(.*spring-boot-starter-parent\)|<version>3.2.0</version>\1|' pom.xml
# Handle the more common XML layout where version is on a separate line after artifactId
# Use a more robust approach: replace version in the parent block
python3 -c "
import re
with open('pom.xml', 'r') as f:
    content = f.read()

# Update parent Spring Boot version (handles multi-line parent block)
content = re.sub(
    r'(<parent>\s*<groupId>org\.springframework\.boot</groupId>\s*<artifactId>spring-boot-starter-parent</artifactId>\s*<version>)2\.7\.\d+(</version>)',
    r'\g<1>3.2.0\2',
    content,
    flags=re.DOTALL
)

# Update Java version
content = re.sub(
    r'<java\.version>[^<]+</java\.version>',
    '<java.version>21</java.version>',
    content
)

# Remove old jjwt monolith dependency
content = re.sub(
    r'\s*<dependency>\s*<groupId>io\.jsonwebtoken</groupId>\s*<artifactId>jjwt</artifactId>\s*<version>[^<]+</version>\s*</dependency>',
    '',
    content,
    flags=re.DOTALL
)

# Remove javax.xml.bind:jaxb-api
content = re.sub(
    r'\s*<dependency>\s*<groupId>javax\.xml\.bind</groupId>\s*<artifactId>jaxb-api</artifactId>\s*<version>[^<]+</version>\s*</dependency>',
    '',
    content,
    flags=re.DOTALL
)

# Add modular JJWT dependencies before </dependencies>
jjwt_deps = '''
        <dependency>
            <groupId>io.jsonwebtoken</groupId>
            <artifactId>jjwt-api</artifactId>
            <version>0.12.6</version>
        </dependency>
        <dependency>
            <groupId>io.jsonwebtoken</groupId>
            <artifactId>jjwt-impl</artifactId>
            <version>0.12.6</version>
            <scope>runtime</scope>
        </dependency>
        <dependency>
            <groupId>io.jsonwebtoken</groupId>
            <artifactId>jjwt-jackson</artifactId>
            <version>0.12.6</version>
            <scope>runtime</scope>
        </dependency>
    '''
content = content.replace('</dependencies>', jjwt_deps + '</dependencies>')

with open('pom.xml', 'w') as f:
    f.write(content)
print('pom.xml updated successfully')
"

# ============================================================
# 3. Namespace migration: javax.* → jakarta.*
# ============================================================
echo "=== Namespace migration ==="
find src -name "*.java" -exec sed -i \
    -e 's/import javax\.persistence/import jakarta.persistence/g' \
    -e 's/import javax\.validation/import jakarta.validation/g' \
    -e 's/import javax\.servlet/import jakarta.servlet/g' \
    {} +

# Verify
remaining=$(grep -rn "import javax\.\(persistence\|validation\|servlet\)" src --include="*.java" || true)
if [ -n "$remaining" ]; then
    echo "WARNING: Remaining javax imports:"
    echo "$remaining"
else
    echo "All javax EE imports migrated to jakarta"
fi

# ============================================================
# 4. Spring Security 6 migration
# ============================================================
echo "=== Spring Security 6 migration ==="

# Find the SecurityConfig file
SECURITY_CONFIG=$(find src -name "SecurityConfig.java" -type f | head -1)
if [ -n "$SECURITY_CONFIG" ]; then
    echo "Migrating $SECURITY_CONFIG"

    python3 -c "
import re

with open('$SECURITY_CONFIG', 'r') as f:
    content = f.read()

# Detect what's imported/used to build a proper replacement
# This script handles the COMMON case. For unusual configs, manual review needed.

# Key replacements in imports
content = content.replace(
    'import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity;',
    'import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;'
)
content = content.replace(
    'import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;',
    ''
)

# Add missing imports if not present
needed_imports = [
    'import org.springframework.security.web.SecurityFilterChain;',
    'import org.springframework.security.authentication.AuthenticationManager;',
    'import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;',
]
for imp in needed_imports:
    if imp not in content:
        # Add after the last existing import
        last_import = content.rfind('import ')
        end_of_line = content.find(';', last_import) + 1
        content = content[:end_of_line] + '\n' + imp + content[end_of_line:]

# Annotation replacement
content = content.replace('@EnableGlobalMethodSecurity(prePostEnabled = true)', '@EnableMethodSecurity')
content = content.replace('@