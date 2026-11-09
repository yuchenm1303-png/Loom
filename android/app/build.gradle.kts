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
        versionCode = 8
        versionName = "0.1.7"
    }

    buildFeatures { buildConfig = true }

    buildTypes {
        all {
            buildConfigField("String", "ACCOUNT_URL", "\"https://account.smirel.com/v1\"")
            buildConfigField("String", "RELAY_URL", "\"wss://loom.smirel.com/api/ws/client\"")
            buildConfigField("boolean", "QA", "false")
        }
        // Runs the real app against android/qa/stack.py on the host; see android/qa/README.md.
        create("qa") {
            initWith(getByName("debug"))
            applicationIdSuffix = ".qa"
            buildConfigField("String", "ACCOUNT_URL", "\"http://10.0.2.2:18787/v1\"")
            buildConfigField("String", "RELAY_URL", "\"ws://10.0.2.2:18790/api/ws/client\"")
            buildConfigField("boolean", "QA", "true")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/web"))
}

// The page is the desktop-react mobile entry: `npm run build:mobile` first.
// Sync, not Copy: bundles from earlier builds must not pile up in the APK.
val copyWeb by tasks.registering(Sync::class) {
    val page = rootProject.file("../desktop-react/dist-mobile")
    doFirst { check(page.resolve("mobile.html").exists()) { "Run npm run build:mobile in desktop-react first." } }
    // Stickers are gitignored and prepared by build:mobile; a bundle built without them shows blank stickers.
    doFirst { check((page.resolve("chat-stickers").listFiles { f -> f.name.endsWith(".webp") }?.size ?: 0) > 0) { "dist-mobile has no chat stickers: run npm run build:mobile in desktop-react." } }
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
