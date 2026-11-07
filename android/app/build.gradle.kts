plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.smirel.loom"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.smirel.loom"
        minSdk = 29
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/web"))
}

// The page is the desktop-react mobile entry: `npm run build:mobile` first.
val copyWeb by tasks.registering(Copy::class) {
    val page = rootProject.file("../desktop-react/dist-mobile")
    doFirst { check(page.resolve("mobile.html").exists()) { "Run npm run build:mobile in desktop-react first." } }
    from(page)
    into(layout.buildDirectory.dir("generated/web/web"))
}
tasks.named("preBuild") { dependsOn(copyWeb) }

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("androidx.webkit:webkit:1.13.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // Decodes on the device; needs no Google Play services, which this phone lacks.
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
}
