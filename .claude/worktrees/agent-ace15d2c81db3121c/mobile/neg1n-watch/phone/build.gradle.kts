plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.mckay.neg1n.phone"
    compileSdk = 34

    defaultConfig {
        // Same applicationId as the wear module, deliberately: Wear OS's
        // Data Layer scopes DataItems/listeners per package identity, so a
        // phone half and watch half of one companion app must share it to
        // see each other's data at all (see DataLayerReader's doc comment
        // for how this was found — different ids meant the data genuinely
        // never crossed devices, confirmed via dumpsys). `namespace` above
        // stays module-specific; only applicationId needs to match.
        applicationId = "com.mckay.neg1n"
        minSdk = 28
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.google.android.gms:play-services-wearable:18.2.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-play-services:1.8.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
}
