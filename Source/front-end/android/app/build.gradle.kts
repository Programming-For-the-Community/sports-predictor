import java.util.Properties

plugins {
    id("com.android.application")
    id("kotlin-android")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Written by mobile_hosting.yml from repository secrets (see ../README.md).
// Without it -- a local `flutter run --release` -- release builds fall back
// to the debug key, which can't update an installed release build.
val keyProperties = Properties().apply {
    val file = rootProject.file("key.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

// Release builds (the arm64 APK CI ships) resolve exactly the versions pinned
// in app/gradle.lockfile; debug/profile builds for local phones and emulators
// stay unlocked. See ../build.gradle.kts for regenerating.
configurations.configureEach {
    if (name.startsWith("release")) resolutionStrategy.activateDependencyLocking()
}

android {
    namespace = "com.professorchaos0802.sportspredictor"
    // The highest SDK/NDK the plugins compile against (flutter_secure_storage
    // and ota_update need SDK 36; most plugins pin NDK 27). Both are
    // backward compatible -- minSdk below still sets the oldest Android.
    compileSdk = 36
    ndkVersion = "27.0.12077973"

    compileOptions {
        // flutter_local_notifications needs java.time on older Android versions.
        isCoreLibraryDesugaringEnabled = true
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }

    kotlinOptions {
        jvmTarget = JavaVersion.VERSION_11.toString()
    }

    defaultConfig {
        applicationId = "com.professorchaos0802.sportspredictor"
        // Android 7.0 -- flutter_secure_storage's Keystore-backed storage.
        minSdk = 24
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        // arm64 only, including plugins' own native libraries.
        ndk { abiFilters += "arm64-v8a" }
    }

    signingConfigs {
        if (keyProperties.isNotEmpty()) {
            create("release") {
                storeFile = file(keyProperties.getProperty("storeFile"))
                storePassword = keyProperties.getProperty("storePassword")
                keyAlias = keyProperties.getProperty("keyAlias")
                keyPassword = keyProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
            // R8 shrinking + obfuscation of the Java/Kotlin code. Classes the
            // manifest names (activities, widget providers, receivers) are
            // kept automatically; proguard-rules.pro holds anything else.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
}

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")
}

flutter {
    source = "../.."
}
