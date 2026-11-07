import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm") version "2.4.10"
    `java-library`
    `maven-publish`
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
                description.set("Official Android / Kotlin client for the LIKYLY recommendations API (public-key surface).")
                url.set("https://likyly.com")
            }
        }
    }
}
