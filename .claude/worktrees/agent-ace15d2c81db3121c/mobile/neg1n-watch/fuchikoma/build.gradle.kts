plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Fuchikoma — native Android front end for dtd (tasks) and janus (timeline).
// Thin client over the Flask JSON APIs that already serve the mobile WEB
// versions on Ix (tools/dtd/dtd.py :5560, tools/janus/mobile.py :5561); no
// business logic lives here, every swipe is one POST to the same endpoint the
// web page calls. Same toolchain as the neg1n phone module on purpose: one
// Gradle project, one JDK, one `./gradlew :fuchikoma:assembleDebug`.
android {
    namespace = "com.mckay.fuchikoma"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.mckay.fuchikoma"
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
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.fragment:fragment-ktx:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // android.jar's org.json is a stub that throws on the JVM; the real
    // library shadows it on the unit-test classpath so the parsers can be
    // tested without an emulator.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
