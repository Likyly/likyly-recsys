import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.4.10"
    `java-library`
    `maven-publish`
    signing
}

group = "com.likyly"
version = "1.0.0"

repositories {
    mavenCentral()
}

dependencies {
    // The only runtime dependency, and one every Android app already has.
    api("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")

    testImplementation(kotlin("test"))
}

kotlin {
    explicitApi()
    compilerOptions {
        // Java 8 bytecode: consumable by every Android app (minSdk 21+) and any JVM.
        jvmTarget.set(JvmTarget.JVM_1_8)
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_1_8
    targetCompatibility = JavaVersion.VERSION_1_8
    withSourcesJar()
    withJavadocJar() // Maven Central wants one; Kotlin sources leave it nearly empty, the README is the guide
}

tasks.test {
    useJUnitPlatform()
}

publishing {
    publications {
        create<MavenPublication>("maven") {
            artifactId = "likyly-android"
            from(components["java"])
            pom {
                name.set("LIKYLY Android SDK")
                description.set("Product recommendations in your Android app (\"You may also like\", \"Recommended for you\"), learned from what your users do. Official LIKYLY client for Android and Kotlin.")
                url.set("https://www.likyly.com")
                licenses { license { name.set("MIT"); url.set("https://opensource.org/licenses/MIT") } }
                developers { developer { name.set("LIKYLY"); url.set("https://www.likyly.com") } }
                scm {
                    connection.set("scm:git:https://github.com/Likyly/likyly-recsys.git")
                    developerConnection.set("scm:git:https://github.com/Likyly/likyly-recsys.git")
                    url.set("https://github.com/Likyly/likyly-recsys/tree/main/sdk/android")
                }
                issueManagement { system.set("GitHub"); url.set("https://github.com/Likyly/likyly-recsys/issues") }
            }
        }
    }
    // Maven Central release: ./gradlew publish -Prelease signs with your GPG key (gpg-agent) and writes the
    // bundle to build/bundle; zip it and upload it on central.sonatype.com.
    repositories { maven { name = "bundle"; url = uri(layout.buildDirectory.dir("bundle")) } }
}

if (providers.gradleProperty("release").isPresent) {
    signing {
        useGpgCmd()
        sign(publishing.publications["maven"])
    }
}
