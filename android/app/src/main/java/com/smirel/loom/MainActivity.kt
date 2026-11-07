package com.smirel.loom

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

private const val PAGE_HOST = "appassets.androidplatform.net"
private const val PAGE_URL = "https://$PAGE_HOST/assets/web/mobile.html"
private const val POLL_MS = 1_500L

class MainActivity : AppCompatActivity(), Relay.Listener {
    private lateinit var web: WebView
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private var pickerCallback: ValueCallback<Array<Uri>>? = null
    private var claimToken: String? = null

    private val scanner = registerForActivityResult(ScanContract()) { result ->
        val contents = result.contents
        if (contents == null) pairing(JSONObject().put("state", "cancelled")) else claim(contents)
    }

    private val filePicker = registerForActivityResult(ActivityResultContracts.GetMultipleContents()) { uris ->
        pickerCallback?.onReceiveValue(uris.toTypedArray())
        pickerCallback = null
    }

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Relay.init(this)
        Notifier.ensureChannels(this)
        web = WebView(this)
        // A WebView ignores its own padding, so the system bars and keyboard inset a frame around it.
        val frame = FrameLayout(this)
        frame.addView(web)
        setContentView(frame)
        ViewCompat.setOnApplyWindowInsetsListener(frame) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        frame.setBackgroundColor(ContextCompat.getColor(this, R.color.canvas))
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.canvas))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
        }
        val assets = WebViewAssetLoader.Builder()
            .setDomain(PAGE_HOST)
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                assets.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.host == PAGE_HOST) return false
                openExternal(request.url.toString())
                return true
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                view: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                pickerCallback?.onReceiveValue(null)
                pickerCallback = callback
                filePicker.launch("*/*")
                return true
            }
        }
        web.addJavascriptInterface(Shell(), "LoomShell")
        web.loadUrl(PAGE_URL)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                web.evaluateJavascript(
                    "!window.dispatchEvent(new CustomEvent('loom:mobile-back',{cancelable:true}))",
                ) { handled -> if (handled != "true") moveTaskToBack(true) }
            }
        })

        // An emulator has no camera to scan with; only the qa build reads this.
        if (BuildConfig.QA) intent.getStringExtra("qa_pair")?.let(::claim)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            permissions.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    override fun onStart() {
        super.onStart()
        Relay.listener = this
        Relay.visible = true
        if (Relay.paired()) {
            Relay.connect()
            RelayService.keepAlive(this)
        }
    }

    override fun onStop() {
        Relay.visible = false
        RelayService.settle(this)
        super.onStop()
    }

    override fun onDestroy() {
        if (Relay.listener === this) Relay.listener = null
        web.destroy()
        super.onDestroy()
    }

    override fun onShellEvent(json: String) {
        web.evaluateJavascript("window.__loomShellEvent&&window.__loomShellEvent(${JSONObject.quote(json)})", null)
    }

    private fun pairing(progress: JSONObject) {
        Relay.emit(JSONObject().put("type", "pairing").put("progress", progress))
    }

    private fun pairingFailed(message: String) {
        pairing(JSONObject().put("state", "failed").put("message", message))
    }

    private fun claim(contents: String) {
        val code = Uri.parse(contents)
        val ticket = code.getQueryParameter("t")
        val deviceId = code.getQueryParameter("d")
        if (code.scheme != "loom" || code.host != "pair" || ticket.isNullOrBlank() || deviceId.isNullOrBlank()) {
            pairingFailed("这不是 Loom 的配对二维码")
            return
        }
        val phoneName = listOf(Build.MANUFACTURER, Build.MODEL).joinToString(" ").trim()
        worker.execute {
            try {
                val claimed = AccountApi.claim(ticket, phoneName)
                val token = claimed.getString("claim_token")
                claimToken = token
                pairing(
                    JSONObject().put("state", "claimed")
                        .put("confirmCode", claimed.getString("confirm_code"))
                        .put("deviceName", claimed.optString("device_name")),
                )
                poll(token, claimed.optString("device_id", deviceId), claimed.optString("device_name"))
            } catch (error: Exception) {
                pairingFailed(error.message ?: "配对失败，请重试")
            }
        }
    }

    private fun poll(token: String, deviceId: String, deviceName: String) {
        while (claimToken == token) {
            Thread.sleep(POLL_MS)
            val answer = AccountApi.poll(token)
            when (answer.optString("state")) {
                "approved" -> {
                    claimToken = null
                    Relay.adopt(
                        Session(
                            answer.getString("access_token"),
                            answer.getString("refresh_token"),
                            System.currentTimeMillis() + answer.getLong("expires_in") * 1000,
                            deviceId,
                            deviceName,
                        ),
                    )
                    pairing(JSONObject().put("state", "approved"))
                    main.post { RelayService.keepAlive(this) }
                    return
                }
                "denied" -> {
                    claimToken = null
                    pairingFailed("电脑上拒绝了这次配对")
                    return
                }
            }
        }
    }

    private fun openExternal(url: String) {
        val target = Uri.parse(url)
        if (target.scheme != "https" && target.scheme != "http") return
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, target)) }
    }

    private fun confirmBiometric(id: Int, reason: String) {
        val answer = { ok: Boolean -> Relay.emit(JSONObject().put("type", "biometric").put("id", id).put("ok", ok)) }
        val allowed = BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
        if (BiometricManager.from(this).canAuthenticate(allowed) != BiometricManager.BIOMETRIC_SUCCESS) {
            answer(false)
            return
        }
        val prompt = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) = answer(true)
                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) = answer(false)
            },
        )
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("确认身份")
                .setSubtitle(reason)
                .setAllowedAuthenticators(allowed)
                .build(),
        )
    }

    /** Everything the page may ask of the app. Calls arrive on a WebView thread. */
    private inner class Shell {
        @JavascriptInterface
        fun state(): String = Relay.stateJson().toString()

        @JavascriptInterface
        fun invoke(id: Int, operation: String, argsJson: String) {
            Relay.invoke(id, operation, JSONArray(argsJson))
        }

        /** The threads the computer is on right now, including ones started before the app opened. */
        @JavascriptInterface
        fun setBusyThreads(idsJson: String) {
            val ids = JSONArray(idsJson)
            main.post {
                Relay.busyThreads.clear()
                for (index in 0 until ids.length()) Relay.busyThreads.add(ids.getString(index))
            }
        }

        @JavascriptInterface
        fun fetchDevices(id: Int) = Relay.fetchDevices(id)

        @JavascriptInterface
        fun connectionLog(): String = Relay.connectionLog()

        @JavascriptInterface
        fun startPairing() {
            main.post {
                scanner.launch(
                    ScanOptions()
                        .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                        .setPrompt("扫描电脑上的二维码")
                        .setBeepEnabled(false)
                        .setOrientationLocked(true),
                )
            }
        }

        @JavascriptInterface
        fun cancelPairing() {
            claimToken = null
            pairing(JSONObject().put("state", "cancelled"))
        }

        @JavascriptInterface
        fun selectDevice(deviceId: String) = Relay.selectDevice(deviceId)

        @JavascriptInterface
        fun unpair() {
            Relay.forget()
            main.post { stopService(Intent(this@MainActivity, RelayService::class.java)) }
        }

        @JavascriptInterface
        fun confirmBiometric(id: Int, reason: String) {
            main.post { this@MainActivity.confirmBiometric(id, reason) }
        }

        @JavascriptInterface
        fun openUrl(url: String) {
            main.post { openExternal(url) }
        }
    }
}
