# JaCoCo Tooling for Branch Coverage Measurement

## Provenance

This directory contains JaCoCo (Java Code Coverage) tools bundled from Maven Central:
- **Organization**: `org.jacoco`
- **Version**: `0.8.12`
- **jacocoagent.jar**: `org.jacoco:org.jacoco.agent` with `runtime` classifier
- **jacococli.jar**: `org.jacoco:org.jacoco.cli` with `nodeps` classifier
- **jacoco-init.gradle**: Custom Gradle init-script for non-invasive instrumentation

## Non-Invasive Design

These tools measure branch coverage **without modifying any Gradle build files** (build.gradle, settings.gradle, gradle.properties) or changing the JDK/toolchain configuration.

### How It Works

1. **jacoco-init.gradle**: An init-script that is loaded via Gradle's `-I` flag at build time
2. **System Properties**: Instrumentation is configured via JVM system properties:
   - `han.jacoco.agent`: Path to the agent JAR
   - `han.jacoco.exec`: Destination for coverage execution data
3. **javaagent Attachment**: The init-script automatically attaches the JaCoCo agent to all Test tasks via `-javaagent` JVM argument
4. **Zero Build.gradle Changes**: No modifications to the project's build.gradle; all configuration happens at invocation time

### Coverage Scope

- **Metric**: Logical **BRANCH** coverage only (never line coverage)
- **Fail-Closed**: No verification = no green light (verified coverage only)

## Usage

To measure branch coverage during test execution:

```bash
gradle -I <path-to-jacoco-init.gradle> \
  -Dhan.jacoco.agent=<path-to-jacocoagent.jar> \
  -Dhan.jacoco.exec=<output-exec-file> \
  test
```

The coverage execution data is written to the specified `.exec` file, which can then be analyzed using `jacococli.jar`.
