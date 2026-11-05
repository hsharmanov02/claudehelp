import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

android {
    namespace = "com.localforge.app"
    compileSdk = 36

    defaultConfig {
        minSdk = 30
        targetSdk = 36

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
        externalNativeBuild {
            cmake {
                arguments += listOf("-DCMAKE_BUILD_TYPE=Release", "-DANDROID_STL=c++_shared")
                cppFlags += listOf("-O3")
            }
        }
    }

    // Two apps from one code base: LocalForge (chat/code assistant) and LocalForge Autopilot (one main command, 24/7).
    // They install side by side, keep their own chats and settings, and share the model files in /sdcard/LocalForge/models.
    flavorDimensions += "app"
    productFlavors {
        create("forge") {
            dimension = "app"
            applicationId = "com.localforge.app"
            versionCode = 11
            versionName = "1.9.0"
            buildConfigField("boolean", "AUTOPILOT", "false")
            resValue("string", "app_name", "LocalForge")
        }
        create("autopilot") {
            dimension = "app"
            applicationId = "com.localforge.autopilot"
            versionCode = 1
            versionName = "1.0.0"
            buildConfigField("boolean", "AUTOPILOT", "true")
            resValue("string", "app_name", "LocalForge Autopilot")
        }
    }

    signingConfigs {
        create("release") {
            // Keystore is created by the build script so every build installs over the last one.
            storeFile = file(System.getenv("LF_KEYSTORE") ?: "localforge.jks")
            storePassword = System.getenv("LF_KEYSTORE_PASS") ?: "localforge"
            keyAlias = "localforge"
            keyPassword = System.getenv("LF_KEYSTORE_PASS") ?: "localforge"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release")
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        // CPU backend variants are loaded from disk at runtime, so native libs must be extracted
        jniLibs.useLegacyPackaging = true
    }
}

// Bundled Python 3.12 so the assistant can run and test its own code on the phone.
chaquopy {
    defaultConfig {
        version = "3.12"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.01.00")
    implementation(composeBom)
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("androidx.webkit:webkit:1.12.1")
    testImplementation("junit:junit:4.13.2")
}
