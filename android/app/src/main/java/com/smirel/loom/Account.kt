package com.smirel.loom

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.security.KeyStore
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

const val ACCOUNT_URL = "https://account.smirel.com/v1"
const val RELAY_URL = "wss://loom.smirel.com/api/ws/client"

data class Session(
    val accessToken: String,
    val refreshToken: String,
    val accessExpiresAt: Long,
    val deviceId: String,
    val deviceName: String,
)

class AccountError(val status: Int, val code: String, message: String) : Exception(message)

/** The phone's session, encrypted with a key that never leaves the Android Keystore. */
class SessionStore(context: Context) {
    private val preferences = context.getSharedPreferences("loom_session", Context.MODE_PRIVATE)

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build(),
        )
        return generator.generateKey()
    }

    fun load(): Session? {
        val stored = preferences.getString("session", null) ?: return null
        return try {
            val raw = Base64.decode(stored, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORM)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, raw, 0, IV_BYTES))
            val json = JSONObject(String(cipher.doFinal(raw, IV_BYTES, raw.size - IV_BYTES), Charsets.UTF_8))
            Session(
                json.getString("access"),
                json.getString("refresh"),
                json.getLong("expires"),
                json.getString("deviceId"),
                json.getString("deviceName"),
            )
        } catch (_: Exception) {
            clear()
            null
        }
    }

    fun save(session: Session) {
        val plain = JSONObject()
            .put("access", session.accessToken)
            .put("refresh", session.refreshToken)
            .put("expires", session.accessExpiresAt)
            .put("deviceId", session.deviceId)
            .put("deviceName", session.deviceName)
            .toString()
            .toByteArray(Charsets.UTF_8)
        val cipher = Cipher.getInstance(TRANSFORM)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val sealed = cipher.iv + cipher.doFinal(plain)
        preferences.edit().putString("session", Base64.encodeToString(sealed, Base64.NO_WRAP)).apply()
    }

    fun clear() {
        preferences.edit().remove("session").apply()
    }

    private companion object {
        const val KEY_ALIAS = "loom_session_key"
        const val TRANSFORM = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
    }
}

/** Blocking calls; always run off the main thread. */
object AccountApi {
    val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .pingInterval(25, TimeUnit.SECONDS)
        .build()

    private val JSON_TYPE = "application/json".toMediaType()

    private fun post(path: String, body: JSONObject): JSONObject = execute(
        Request.Builder()
            .url("$ACCOUNT_URL$path")
            .post(body.toString().toRequestBody(JSON_TYPE))
            .header("Accept", "application/json")
            .build(),
    )

    fun devices(accessToken: String): JSONObject = execute(
        Request.Builder()
            .url("$ACCOUNT_URL/auth/devices")
            .header("Accept", "application/json")
            .header("Authorization", "Bearer $accessToken")
            .build(),
    )

    private fun execute(request: Request): JSONObject {
        http.newCall(request).execute().use { response ->
            val text = response.body?.string().orEmpty()
            val json = runCatching { JSONObject(text) }.getOrDefault(JSONObject())
            if (!response.isSuccessful) {
                val error = json.optJSONObject("error")
                throw AccountError(
                    response.code,
                    error?.optString("code").orEmpty(),
                    error?.optString("message")?.takeIf { it.isNotBlank() } ?: "账号服务暂时不可用（${response.code}）",
                )
            }
            return json
        }
    }

    fun claim(ticket: String, phoneName: String): JSONObject =
        post("/auth/mobile-pair/claim", JSONObject().put("pairing_ticket", ticket).put("phone_name", phoneName))

    fun poll(claimToken: String): JSONObject =
        post("/auth/mobile-pair/poll", JSONObject().put("claim_token", claimToken))

    fun refresh(session: Session): Session {
        val tokens = post("/auth/refresh", JSONObject().put("refresh_token", session.refreshToken))
        return session.copy(
            accessToken = tokens.getString("access_token"),
            refreshToken = tokens.getString("refresh_token"),
            accessExpiresAt = System.currentTimeMillis() + tokens.getLong("expires_in") * 1000,
        )
    }

    fun logout(session: Session) {
        runCatching { post("/auth/logout", JSONObject().put("refresh_token", session.refreshToken)) }
    }
}
