package com.smirel.loom

import android.content.Context
import android.os.Handler
import android.os.Looper
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * The one connection to the selected computer. It lives outside the page so the
 * tokens stay native and a running turn keeps reporting while the page sleeps.
 */
object Relay {
    interface Listener {
        fun onShellEvent(json: String)
    }

    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var store: SessionStore
    private lateinit var appContext: Context

    private var session: Session? = null
    private var socket: WebSocket? = null
    private var socketState = "closed"
    private var online = false
    private var devices = JSONArray()
    private var wanted = false
    private var retryMs = RETRY_MIN_MS
    private var generation = 0
    private var history = JSONArray()

    /** Threads the computer is working on, so the service knows when it may rest. */
    val busyThreads: MutableSet<String> = mutableSetOf()
    var listener: Listener? = null
    var visible = false

    fun init(context: Context) {
        if (::store.isInitialized) return
        appContext = context.applicationContext
        store = SessionStore(appContext)
        session = store.load()
        history = runCatching { JSONArray(preferences().getString(LOG_KEY, "[]")) }.getOrDefault(JSONArray())
    }

    private fun preferences() = appContext.getSharedPreferences("loom_connection", Context.MODE_PRIVATE)

    /** Why the connection came and went, kept on the phone so a drop can be explained later. */
    fun record(event: String, detail: String = "") {
        val entry = JSONObject().put("at", System.currentTimeMillis()).put("event", event).put("detail", detail.take(LOG_DETAIL_LIMIT))
        history.put(entry)
        while (history.length() > LOG_LIMIT) history.remove(0)
        preferences().edit().putString(LOG_KEY, history.toString()).apply()
    }

    fun connectionLog(): String = history.toString()

    /** The account's devices, answered as a shell event. Needs a fresh token, so it runs off the main thread. */
    fun fetchDevices(id: Int) {
        val current = session
        worker.execute {
            val event = JSONObject().put("type", "devices").put("id", id)
            try {
                if (current == null) throw IllegalStateException("not paired")
                event.put("devices", AccountApi.devices(freshSession(current).accessToken))
            } catch (error: Exception) {
                event.put("error", error.message ?: "读取设备失败")
            }
            emit(event)
        }
    }

    fun paired() = session != null

    fun stateJson(): JSONObject = JSONObject()
        .put("paired", session != null)
        .put("deviceId", session?.deviceId.orEmpty())
        .put("deviceName", session?.deviceName.orEmpty())
        .put("socket", socketState)
        .put("online", online)
        .put("devices", devices)

    private fun publishState() {
        emit(JSONObject().put("type", "state").put("state", stateJson()))
    }

    fun emit(event: JSONObject) {
        val text = event.toString()
        main.post { listener?.onShellEvent(text) }
    }

    fun adopt(next: Session) {
        store.save(next)
        session = next
        devices = JSONArray()
        online = false
        retryMs = RETRY_MIN_MS
        connect()
    }

    fun connect() {
        main.post {
            wanted = true
            if (socket == null) open()
        }
    }

    fun rest() {
        main.post {
            wanted = false
            if (socket != null) record("rest")
            close()
            publishState()
        }
    }

    fun forget() {
        val ending = session
        session = null
        store.clear()
        rest()
        busyThreads.clear()
        if (ending != null) worker.execute { AccountApi.logout(ending) }
    }

    fun selectDevice(deviceId: String) {
        val current = session ?: return
        var name = current.deviceName
        for (index in 0 until devices.length()) {
            val device = devices.optJSONObject(index) ?: continue
            if (device.optString("id") == deviceId) name = device.optString("name", name)
        }
        val next = current.copy(deviceId = deviceId, deviceName = name)
        store.save(next)
        session = next
        main.post {
            close()
            busyThreads.clear()
            retryMs = RETRY_MIN_MS
            if (wanted) open()
        }
    }

    /** False when the frame could not be queued (socket gone, or over OkHttp's 16 MB queue). */
    fun send(frame: JSONObject): Boolean = socket?.send(frame.toString()) == true

    fun invoke(id: Int, operation: String, args: JSONArray) {
        val frame = JSONObject().put("type", "invoke").put("id", id).put("operation", operation).put("args", args)
        if (!send(frame)) {
            emit(
                JSONObject().put("type", "invoke_result").put("id", id).put(
                    "error",
                    JSONObject().put("code", "HOST_OFFLINE").put("message", "和电脑的连接已断开"),
                ),
            )
        }
    }

    private fun close() {
        generation += 1
        socket?.close(1000, null)
        socket = null
        socketState = "closed"
        online = false
    }

    private fun open() {
        val current = session ?: return
        generation += 1
        val attempt = generation
        socketState = "connecting"
        publishState()
        worker.execute {
            val fresh = try {
                freshSession(current)
            } catch (error: AccountError) {
                main.post { if (attempt == generation) onAuthFailure(error) }
                return@execute
            } catch (_: Exception) {
                main.post { if (attempt == generation) retry() }
                return@execute
            }
            main.post { if (attempt == generation) dial(fresh, attempt) }
        }
    }

    private fun freshSession(current: Session): Session {
        if (current.accessExpiresAt > System.currentTimeMillis() + REFRESH_MARGIN_MS) return current
        val next = AccountApi.refresh(current)
        store.save(next)
        session = next
        return next
    }

    private fun dial(current: Session, attempt: Int) {
        val request = Request.Builder()
            .url("$RELAY_URL?device=${java.net.URLEncoder.encode(current.deviceId, "UTF-8")}")
            .header("Authorization", "Bearer ${current.accessToken}")
            .build()
        socket = AccountApi.http.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                main.post {
                    if (attempt != generation) return@post
                    retryMs = RETRY_MIN_MS
                    socketState = "open"
                    record("connected")
                    publishState()
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                main.post { if (attempt == generation) onFrame(text) }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                main.post {
                    if (attempt != generation) return@post
                    record("closed", "$code $reason".trim())
                    onDropped(code == CLOSE_UNAUTHORIZED)
                }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                main.post {
                    if (attempt != generation) return@post
                    record("failed", response?.code?.toString() ?: t.javaClass.simpleName)
                    onDropped(response?.code in setOf(401, 403))
                }
            }
        })
    }

    private fun onDropped(unauthorized: Boolean) {
        socket = null
        socketState = "closed"
        online = false
        publishState()
        val current = session
        if (unauthorized && current != null) {
            // The access token may simply have expired; only a failed refresh ends pairing.
            val forced = current.copy(accessExpiresAt = 0)
            session = forced
        }
        retry()
    }

    private fun onAuthFailure(error: AccountError) {
        if (error.status == 401 || error.status == 403) forget() else retry()
    }

    private fun retry() {
        if (!wanted || session == null) return
        val delay = retryMs
        retryMs = (retryMs * 1.6).toLong().coerceAtMost(RETRY_MAX_MS)
        val attempt = generation
        main.postDelayed({ if (attempt == generation && wanted && socket == null) open() }, delay)
    }

    private fun onFrame(text: String) {
        val frame = runCatching { JSONObject(text) }.getOrNull() ?: return
        when (frame.optString("type")) {
            "device_status" -> {
                val next = frame.optBoolean("online")
                if (next != online) record(if (next) "computer-online" else "computer-offline")
                online = next
                devices = frame.optJSONArray("devices") ?: JSONArray()
                publishState()
            }
            "invoke_result" -> emit(frame)
            "notification" -> {
                val payload = frame.optJSONObject("payload")
                if (payload != null) Notifier.onRuntimeEvent(appContext, session?.deviceName.orEmpty(), payload)
                emit(frame)
            }
        }
    }

    private const val LOG_KEY = "events"
    private const val LOG_LIMIT = 40
    private const val LOG_DETAIL_LIMIT = 80
    private const val RETRY_MIN_MS = 1_500L
    private const val RETRY_MAX_MS = 30_000L
    private const val REFRESH_MARGIN_MS = 60_000L
    private const val CLOSE_UNAUTHORIZED = 4401
}
