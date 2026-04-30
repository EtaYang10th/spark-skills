---
title: Spring Boot 2.7 to 3.2 / Java 8 to 21 / Jakarta Migration
category: spring-boot-jakarta-migration
version: 1
tags:
  - spring-boot
  - jakarta-ee
  - java-migration
  - spring-security
  - hibernate
  - restclient
applies_when:
  - Migrating a Spring Boot 2.x project to Spring Boot 3.x
  - Upgrading Java 8/11/17 to Java 21
  - Replacing javax namespace with jakarta namespace
  - Modernizing Spring Security configuration
  - Replacing RestTemplate with RestClient
---

# Spring Boot 2.7 → 3.2 / Java 8 → 21 / Jakarta EE Migration

## Overview

Spring Boot 3.x requires Jakarta EE 9+ (the `jakarta.*` namespace), Java 17+, Spring Security 6, and Hibernate 6. This skill covers the complete migration of a typical REST microservice — models, DTOs, controllers, services, security config, JWT dependencies, and application properties.

The migration touches every layer of the application. The key insight is that changes are mechanical but interconnected: a missed `javax` import will cause compile failures, and Spring Security's API has changed structurally (not just renamed).

## High-Level Workflow

1. **Explore the project** — Identify all Java source files, `pom.xml`, and `application.properties`/`application.yml`. Understand the dependency tree and which `javax` packages are in use.
2. **Update `pom.xml`** — Upgrade Spring Boot parent, Java version, and replace/remove incompatible dependencies (jjwt, jaxb-api, etc.).
3. **Migrate all `javax.*` imports to `jakarta.*`** — Every Java file that uses `javax.persistence`, `javax.validation`, `javax.servlet`, or `javax.annotation` must be updated.
4. **Rewrite Spring Security configuration** — Remove `WebSecurityConfigurerAdapter`, switch to `SecurityFilterChain` bean, update annotations and matcher methods.
5. **Migrate RestTemplate to RestClient** — Replace `RestTemplate` usage with Spring 6's `RestClient` builder API.
6. **Update Hibernate dialect in properties** — Hibernate 6 changed dialect class names.
7. **Compile and test** — Run `mvn clean compile` then `mvn test`. Fix any remaining issues.

## Step 1: Explore the Project

Before changing anything, map out what exists. You need to know every file that contains `javax` imports and understand the security configuration style.

```bash
# List all Java source files
find /workspace -name "*.java" | sort

# Check the current pom.xml for Spring Boot version, Java version, dependencies
cat /workspace/pom.xml

# Find all javax usages to know the scope of namespace migration
grep -rn "javax\." /workspace/src --include="*.java" | grep -v "target/"

# Check application properties
find /workspace/src -name "application*.properties" -o -name "application*.yml" | xargs cat 2>/dev/null

# Read every Java file to understand the full codebase
for f in $(find /workspace/src/main -name "*.java" | sort); do
  echo "=== $f ==="
  cat "$f"
  echo
done

# Also read test files — you need to know what the tests expect
for f in $(find /workspace/src/test -name "*.java" | sort); do
  echo "=== $f ==="
  cat "$f"
  echo
done
```

Key things to note during exploration:
- Which `javax` sub-packages are used: `persistence`, `validation`, `servlet`, `annotation`?
- Does `SecurityConfig` extend `WebSecurityConfigurerAdapter`?
- Does it use `@EnableGlobalMethodSecurity`?
- Does it use `antMatchers()`, `mvcMatchers()`, or `regexMatchers()`?
- Is `RestTemplate` used anywhere?
- Which JWT library is used (`io.jsonwebtoken:jjwt` monolithic vs split)?
- Is `javax.xml.bind:jaxb-api` in the dependencies?

## Step 2: Update `pom.xml`

This is the foundation. Everything else depends on getting the dependency tree right.

### 2a: Spring Boot Parent and Java Version

```xml
<!-- BEFORE -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>2.7.18</version>
    <relativePath/>
</parent>

<properties>
    <java.version>1.8</java.version>
</properties>

<!-- AFTER -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
    <relativePath/>
</parent>

<properties>
    <java.version>21</java.version>
</properties>
```

### 2b: Replace the Monolithic JJWT Dependency

The old `io.jsonwebtoken:jjwt:0.9.x` is incompatible with Jakarta EE. It must be replaced with the modern split artifacts (0.12.x).

```xml
<!-- REMOVE this -->
<dependency>
    <groupId>io.jsonwebtoken</groupId>
    <artifactId>jjwt</artifactId>
    <version>0.9.1</version>
</dependency>

<!-- ADD these three -->
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

### 2c: Remove `javax.xml.bind:jaxb-api`

Spring Boot 3.2 uses Jakarta XML Binding. The old `javax.xml.bind:jaxb-api` must be removed entirely. If JAXB is actually needed at runtime, replace with `jakarta.xml.bind:jakarta.xml.bind-api`, but most JWT-based services don't need it once jjwt is upgraded.

```xml
<!-- REMOVE this entirely -->
<dependency>
    <groupId>javax.xml.bind</groupId>
    <artifactId>jaxb-api</artifactId>
    <version>2.3.1</version>
</dependency>
```

### 2d: Verify Other Dependencies

Spring Boot 3.2's starter parent manages most transitive dependencies. Check for any manually-versioned dependencies that might conflict:

- `spring-boot-starter-data-jpa` — managed by parent, no version needed
- `spring-boot-starter-security` — managed by parent
- `spring-boot-starter-validation` — managed by parent (provides Jakarta Validation)
- `spring-boot-starter-web` — managed by parent
- `h2` or other test databases — managed by parent

### Complete pom.xml Example

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         https://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.2.0</version>
        <relativePath/>
    </parent>

    <groupId>com.example</groupId>
    <artifactId>user-service</artifactId>
    <version>0.0.1-SNAPSHOT</version>
    <name>user-service</name>

    <properties>
        <java.version>21</java.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-web</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-data-jpa</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-security</artifactId>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-validation</artifactId>
        </dependency>

        <!-- JWT - modern split artifacts -->
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

        <!-- Test / DB -->
        <dependency>
            <groupId>com.h2database</groupId>
            <artifactId>h2</artifactId>
            <scope>runtime</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-test</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.springframework.security</groupId>
            <artifactId>spring-security-test</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-maven-plugin</artifactId>
            </plugin>
        </plugins>
    </build>
</project>
```

## Step 3: Migrate All `javax.*` Imports to `jakarta.*`

This is the most pervasive change. Every Java file must be checked. The mapping is straightforward:

| Old (`javax.*`)                        | New (`jakarta.*`)                        |
|----------------------------------------|------------------------------------------|
| `javax.persistence.*`                  | `jakarta.persistence.*`                  |
| `javax.validation.*`                   | `jakarta.validation.*`                   |
| `javax.validation.constraints.*`       | `jakarta.validation.constraints.*`       |
| `javax.servlet.*`                      | `jakarta.servlet.*`                      |
| `javax.servlet.http.*`                 | `jakarta.servlet.http.*`                 |
| `javax.annotation.*`                   | `jakarta.annotation.*`                   |

### Entity / Model Classes

```java
// BEFORE
import javax.persistence.*;
import javax.validation.constraints.*;

// AFTER
import jakarta.persistence.*;
import jakarta.validation.constraints.*;
```

Full example for a typical `User` entity:

```java
package com.example.userservice.model;

import jakarta.persistence.*;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.Set;

@Entity
@Table(name = "users")
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @NotBlank
    @Size(min = 3, max = 50)
    @Column(unique = true)
    private String username;

    @NotBlank
    @Email
    @Column(unique = true)
    private String email;

    @NotBlank
    private String password;

    @ElementCollection(fetch = FetchType.EAGER)
    @CollectionTable(name = "user_roles", joinColumns = @JoinColumn(name = "user_id"))
    @Enumerated(EnumType.STRING)
    private Set<Role> roles = new HashSet<>();

    private boolean enabled = true;

    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    // getters and setters...
}
```

### DTO Classes with Validation

```java
package com.example.userservice.dto;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public class CreateUserRequest {

    @NotBlank(message = "Username is required")
    @Size(min = 3, max = 50, message = "Username must be between 3 and 50 characters")
    private String username;

    @NotBlank(message = "Email is required")
    @Email(message = "Email should be valid")
    private String email;

    @NotBlank(message = "Password is required")
    @Size(min = 6, message = "Password must be at least 6 characters")
    private String password;

    // getters and setters...
}
```

### Controller Classes

```java
package com.example.userservice.controller;

import jakarta.validation.Valid;
// ... rest of imports
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/users")
public class UserController {

    // Use @Valid from jakarta.validation, not javax.validation
    @PostMapping
    public ResponseEntity<UserDTO> createUser(@Valid @RequestBody CreateUserRequest request) {
        // ...
    }
}
```

### Servlet-Related Classes (Filters, Exception Handlers)

```java
// BEFORE
import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

// AFTER
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
```

### Bulk Verification Command

After making all changes, verify no `javax` imports remain (excluding `javax.crypto` and `javax.net.ssl` which are part of the JDK, not Jakarta EE):

```bash
# This should return NO results for persistence, validation, servlet, annotation
grep -rn "import javax\.\(persistence\|validation\|servlet\|annotation\)" /workspace/src --include="*.java"
```

## Step 4: Rewrite Spring Security Configuration

This is the most structurally complex change. Spring Security 6 removed `WebSecurityConfigurerAdapter` entirely.

### Key Changes

| Old (Spring Security 5 / Boot 2.7)         | New (Spring Security 6 / Boot 3.2)          |
|---------------------------------------------|----------------------------------------------|
| `extends WebSecurityConfigurerAdapter`      | Standalone `@Configuration` class            |
| `@Override configure(HttpSecurity http)`    | `@Bean SecurityFilterChain filterChain(...)` |
| `@EnableGlobalMethodSecurity(prePostEnabled = true)` | `@EnableMethodSecurity`             |
| `http.antMatchers("/path")`                 | `http.requestMatchers("/path")`              |
| `http.authorizeRequests()`                  | `http.authorizeHttpRequests()`               |
| Chained method style                        | Lambda DSL style                             |

### Complete SecurityConfig Example

```java
package com.example.userservice.config;

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

    private final JwtAuthenticationFilter jwtAuthenticationFilter;
    private final JwtAuthenticationEntryPoint jwtAuthenticationEntryPoint;

    public SecurityConfig(JwtAuthenticationFilter jwtAuthenticationFilter,
                          JwtAuthenticationEntryPoint jwtAuthenticationEntryPoint) {
        this.jwtAuthenticationFilter = jwtAuthenticationFilter;
        this.jwtAuthenticationEntryPoint = jwtAuthenticationEntryPoint;
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            .csrf(csrf -> csrf.disable())
            .exceptionHandling(ex -> ex
                .authenticationEntryPoint(jwtAuthenticationEntryPoint)
            )
            .sessionManagement(session -> session
                .sessionCreationPolicy(SessionCreationPolicy.STATELESS)
            )
            .authorizeHttpRequests(auth -> auth
                // Use requestMatchers instead of antMatchers
                .requestMatchers("/api/auth/**").permitAll()
                .requestMatchers("/api/public/**").permitAll()
                .requestMatchers("/h2-console/**").permitAll()
                .anyRequest().authenticated()
            )
            .headers(headers -> headers
                .frameOptions(frame -> frame.sameOrigin())
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

### Critical Details

1. `@EnableMethodSecurity` enables `@PreAuthorize` and `@PostAuthorize` by default — no need for `prePostEnabled = true`.
2. The lambda DSL (`csrf(csrf -> csrf.disable())`) is required. The old chained style (`csrf().disable()`) is deprecated and may not compile.
3. `authorizeHttpRequests()` replaces `authorizeRequests()`. The inner lambda uses `requestMatchers()` instead of `antMatchers()`.
4. `headers().frameOptions().sameOrigin()` becomes `headers(h -> h.frameOptions(f -> f.sameOrigin()))`.

## Step 5: Migrate RestTemplate to RestClient

Spring 6.1 introduced `RestClient` as the modern synchronous HTTP client. It replaces `RestTemplate` with a fluent builder API.

```java
package com.example.userservice.service;

import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

@Service
public class ExternalApiService {

    private final RestClient restClient;

    public ExternalApiService(RestClient.Builder restClientBuilder) {
        this.restClient = restClientBuilder
                .baseUrl("https://api.example.com")
                .build();
    }

    public String getUserProfile(String userId) {
        return restClient.get()
                .uri("/users/{id}/profile", userId)
                .retrieve()
                .body(String.class);
    }

    public <T> T getResource(String path, Class<T> responseType) {
        return restClient.get()
                .uri(path)
                .retrieve()
                .body(responseType);
    }

    public <T> T postResource(String path, Object body, Class<T> responseType) {
        return restClient.post()
                .uri(path)
                .body(body)
                .retrieve()
                .body(responseType);
    }
}
```

Key points:
- Inject `RestClient.Builder` (auto-configured by Spring Boot) instead of creating `RestTemplate` manually.
- The fluent API: `.get()` / `.post()` → `.uri()` → `.body()` (for POST) → `.retrieve()` → `.body(Type.class)`.
- `RestClient.Builder` is the Spring Boot auto-configured builder — it picks up any `RestClientCustomizer` beans.

## Step 6: Update Hibernate Dialect in Properties

Hibernate 6 renamed its dialect classes. If your `application.properties` or `application.yml` explicitly sets the dialect, update it:

```properties
# BEFORE (Hibernate 5)
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.H2Dialect
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.MySQL8Dialect
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.PostgreSQLDialect

# AFTER (Hibernate 6) — best practice: remove it entirely and let Hibernate auto-detect
# If you must specify it:
spring.jpa.properties.hibernate.dialect=org.hibernate.dialect.H2Dialect
# (H2Dialect still exists in Hibernate 6, but MySQL/PostgreSQL changed)
# MySQL: org.hibernate.dialect.MySQLDialect
# PostgreSQL: org.hibernate.dialect.PostgreSQLDialect
```

The recommended approach for Hibernate 6 is to remove the explicit dialect setting and let Hibernate auto-detect from the JDBC URL. This avoids breakage.

Also check for and update the `open-in-view` warning:

```properties
# Suppress the open-in-view warning (or explicitly disable OSIV)
spring.jpa.open-in-view=false
```

## Step 7: Compile and Test

```bash
# First compile — catches import errors, missing classes, API changes
cd /workspace && mvn clean compile 2>&1

# Then run tests — catches runtime/integration issues
cd /workspace && mvn test 2>&1
```

If compilation fails, the error messages will point to specific files and lines. Common compile errors after migration:

| Error | Cause | Fix |
|-------|-------|-----|
| `package javax.persistence does not exist` | Missed a file | Replace with `jakarta.persistence` |
| `cannot find symbol: method antMatchers` | Security config not updated | Use `requestMatchers` |
| `cannot find symbol: class WebSecurityConfigurerAdapter` | Security config not rewritten | Use `SecurityFilterChain` bean |
| `method authorizeRequests() is not available` | Old security API | Use `authorizeHttpRequests()` |
| `cannot find symbol: method setClaims` | Old jjwt API (0.9.x) | Update to jjwt 0.12.x builder API |

## JJWT API Migration (0.9.x → 0.12.x)

If the project has a JWT utility class, the builder API changed:

```java
// BEFORE (jjwt 0.9.x)
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;

String token = Jwts.builder()
    .setSubject(username)
    .setIssuedAt(new Date())
    .setExpiration(new Date(System.currentTimeMillis() + expiration))
    .signWith(SignatureAlgorithm.HS512, secretKeyString)
    .compact();

Claims claims = Jwts.parser()
    .setSigningKey(secretKeyString)
    .parseClaimsJws(token)
    .getBody();

// AFTER (jjwt 0.12.x)
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import javax.crypto.SecretKey;

// Create a proper SecretKey from your secret string
SecretKey key = Keys.hmacShaKeyFor(secretKeyString.getBytes(StandardCharsets.UTF_8));

String token = Jwts.builder()
    .subject(username)
    .issuedAt(new Date())
    .expiration(new Date(System.currentTimeMillis() + expiration))
    .signWith(key)
    .compact();

Claims claims = Jwts.parser()
    .verifyWith(key)
    .build()
    .parseSignedClaims(token)
    .getPayload();
```

Key changes in jjwt 0.12.x:
- `setSubject()` → `subject()`, `setIssuedAt()` → `issuedAt()`, `setExpiration()` → `expiration()`
- `signWith(algorithm, stringKey)` → `signWith(SecretKey)` (use `Keys.hmacShaKeyFor()`)
- `Jwts.parser()` → `Jwts.parser().verifyWith(key).build()`
- `parseClaimsJws()` → `parseSignedClaims()`
- `.getBody()` → `.getPayload()`

Note: `javax.crypto.SecretKey` is a JDK class — it stays as `javax.crypto`, NOT `jakarta.crypto`.

## Common Pitfalls

1. **Forgetting `javax.servlet` in filter classes.** Developers often update entity and DTO imports but miss servlet-related classes like `HttpServletRequest`, `HttpServletResponse`, `FilterChain`, and `ServletException` in JWT filters and exception handlers. Grep for ALL `javax.` occurrences.

2. **Using `antMatchers` instead of `requestMatchers`.** This is the single most common compile error in security config migration. The method was renamed, not just deprecated.

3. **Keeping the old monolithic `jjwt` dependency.** The `io.jsonwebtoken:jjwt:0.9.x` artifact depends on `javax.xml.bind` internally. Even if you fix all your own imports, this dependency will cause runtime `ClassNotFoundException` for `javax.xml.bind.DatatypeConverter`. You must switch to the split artifacts (0.12.x).

4. **Not removing `jaxb-api`.** The `javax.xml.bind:jaxb-api` dependency pulls in the old `javax` namespace. Remove it. If XML binding is needed, use `jakarta.xml.bind:jakarta.xml.bind-api`.

5. **Mixing old and new security DSL.** Don't mix `http.csrf().disable()` (old chained style) with lambda DSL. Use lambda DSL consistently: `http.csrf(csrf -> csrf.disable())`.

6. **Forgetting `@EnableMethodSecurity` replaces `@EnableGlobalMethodSecurity`.** The old annotation won't compile with Spring Security 6. The new one enables `@PreAuthorize`/`@PostAuthorize` by default without needing `prePostEnabled = true`.

7. **Leaving explicit Hibernate dialect that no longer exists.** Hibernate 6 renamed some dialect classes. Safest approach: remove the explicit dialect and let auto-detection work.

8. **Not reading test files before making changes.** Tests may assert specific behaviors or use specific Spring Security test utilities. Read them first to avoid breaking test expectations.

9. **Confusing `javax.crypto` with Jakarta migration.** `javax.crypto.*` and `javax.net.ssl.*` are part of the JDK, not Jakarta EE. They do NOT get renamed to `jakarta`. Only `javax.persistence`, `javax.validation`, `javax.servlet`, `javax.annotation`, `javax.xml.bind`, etc. are Jakarta EE packages.

10. **Forgetting to inject `RestClient.Builder` properly.** Spring Boot auto-configures a `RestClient.Builder` bean. Inject it via constructor, don't create `RestClient.create()` manually if you want to benefit from auto-configuration (interceptors, error handlers, etc.).

## Quick Reference: Files to Change

For a typical user management microservice, expect to modify:

| File | Changes |
|------|---------|
| `pom.xml` | Boot 3.2, Java 21, jjwt split, remove jaxb-api |
| `User.java` (entity) | `javax.persistence` → `jakarta.persistence`, `javax.validation` → `jakarta.validation` |
| `Role.java` (enum) | Usually no imports to change |
| `CreateUserRequest.java` (DTO) | `javax.validation` → `jakarta.validation` |
| `UserDTO.java` (DTO) | May have validation annotations |
| `UserController.java` | `javax.validation.Valid` → `jakarta.validation.Valid` |
| `UserService.java` | `javax.persistence` → `jakarta.persistence` (if using `EntityManager`) |
| `SecurityConfig.java` | Full rewrite: `SecurityFilterChain` bean, `@EnableMethodSecurity`, `requestMatchers`, lambda DSL |
| `JwtAuthenticationFilter.java` | `javax.servlet` → `jakarta.servlet` |
| `JwtAuthenticationEntryPoint.java` | `javax.servlet` → `jakarta.servlet` |
| `GlobalExceptionHandler.java` | `javax.servlet` → `jakarta.servlet` (if used), `javax.validation` → `jakarta.validation` |
| `ExternalApiService.java` | `RestTemplate` → `RestClient` |
| `JwtTokenProvider.java` / `JwtUtils.java` | jjwt 0.12.x API changes |
| `application.properties` | Hibernate dialect update |