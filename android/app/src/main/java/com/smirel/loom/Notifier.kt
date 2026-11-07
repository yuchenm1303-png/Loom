package com.smirel.loom

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import org.json.JSONArray
import org.json.JSONObject

/**
 * Notifications are written here from what arrived over Loom's own connection, so a
 * command never passes through a push vendor. An "allow once" needs the phone unlocked.
 */
object Notifier {
    const val CHANNEL_WORK = "loom_work"
    const val CHANNEL_NEEDS_YOU = "loom_needs_you"
    const val CHANNEL_DONE = "loom_done"
    private const val ONGOING_ID = 1

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannels(
            listOf(
                NotificationChannel(CHANNEL_WORK, "运行中的任务", NotificationManager.IMPORTANCE_LOW),
                NotificationChannel(CHANNEL_NEEDS_YOU, "需要你批准", NotificationManager.IMPORTANCE_HIGH),
                NotificationChannel(CHANNEL_DONE, "任务结束", NotificationManager.IMPORTANCE_DEFAULT),
            ),
        )
    }

    private fun openApp(context: Context): PendingIntent = PendingIntent.getActivity(
        context,
        0,
        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun ongoing(context: Context): Notification = Notification.Builder(context, CHANNEL_WORK)
        .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
        .setContentTitle("Loom 已连接电脑")
        .setContentText("任务运行时保持连接")
        .setContentIntent(openApp(context))
        .setOngoing(true)
        .build()

    private fun commandOf(approval: JSONObject): String {
        val arguments = approval.optJSONObject("arguments")
        return arguments?.optString("command")?.takeIf { it.isNotBlank() }
            ?: approval.optString("reason").takeIf { it.isNotBlank() }
            ?: approval.optString("toolName")
    }

    fun onRuntimeEvent(context: Context, deviceName: String, payload: JSONObject) {
        val params = payload.optJSONObject("params") ?: JSONObject()
        val threadId = params.optString("threadId")
        when (payload.optString("method")) {
            "turn/started" -> {
                Relay.busyThreads.add(threadId)
                RelayService.keepAlive(context)
            }
            "approval/requested" -> {
                Relay.busyThreads.add(threadId)
                RelayService.keepAlive(context)
                if (!Relay.visible) needsYou(context, deviceName, params)
            }
            "turn/completed" -> {
                Relay.busyThreads.remove(threadId)
                if (!Relay.visible) {
                    val status = params.optJSONObject("turn")?.optString("status").orEmpty()
                    finished(context, deviceName, threadId, status)
                }
                RelayService.settle(context)
            }
        }
    }

    private fun needsYou(context: Context, deviceName: String, params: JSONObject) {
        val approval = params.optJSONObject("approval") ?: return
        val threadId = params.optString("threadId")
        val id = threadId.hashCode()
        val allow = Intent(context, ApprovalReceiver::class.java)
            .putExtra("notificationId", id)
            .putExtra("threadId", threadId)
            .putExtra("turnId", params.optString("turnId"))
            .putExtra("requestId", approval.optString("requestId"))
            .putExtra("callId", approval.optString("callId"))
        val pending = PendingIntent.getBroadcast(
            context,
            id,
            allow,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val action = Notification.Action.Builder(null, "允许一次", pending).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) setAuthenticationRequired(true)
        }.build()
        val command = commandOf(approval)
        val notification = Notification.Builder(context, CHANNEL_NEEDS_YOU)
            .setSmallIcon(android.R.drawable.stat_sys_warning)
            .setContentTitle("${deviceName.ifBlank { "电脑" }}需要你批准")
            .setContentText(command)
            .setStyle(Notification.BigTextStyle().bigText(command))
            .setContentIntent(openApp(context))
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setAutoCancel(true)
            .addAction(action)
            .build()
        runCatching { context.getSystemService(NotificationManager::class.java).notify(id, notification) }
    }

    private fun finished(context: Context, deviceName: String, threadId: String, status: String) {
        val failed = status == "failed"
        val notification = Notification.Builder(context, CHANNEL_DONE)
            .setSmallIcon(android.R.drawable.stat_sys_upload_done)
            .setContentTitle(if (failed) "任务没有完成" else "任务已完成")
            .setContentText(deviceName.ifBlank { "电脑" })
            .setContentIntent(openApp(context))
            .setAutoCancel(true)
            .build()
        runCatching { context.getSystemService(NotificationManager::class.java).notify(threadId.hashCode(), notification) }
    }

    fun cancel(context: Context, id: Int) {
        context.getSystemService(NotificationManager::class.java).cancel(id)
    }

    fun start(service: Service) {
        val notification = ongoing(service)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            service.startForeground(ONGOING_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            service.startForeground(ONGOING_ID, notification)
        }
    }
}

/** Keeps the process, and so the connection, alive while the computer is working. */
class RelayService : Service() {
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        Notifier.ensureChannels(this)
        Notifier.start(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Relay.init(this)
        Relay.connect()
        return START_NOT_STICKY
    }

    companion object {
        fun keepAlive(context: Context) {
            runCatching { context.startForegroundService(Intent(context, RelayService::class.java)) }
        }

        /** Nothing to watch and nobody looking: drop the connection until the app opens. */
        fun settle(context: Context) {
            if (Relay.visible || Relay.busyThreads.isNotEmpty()) return
            context.stopService(Intent(context, RelayService::class.java))
            Relay.rest()
        }
    }
}

class ApprovalReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val params = JSONObject()
            .put("threadId", intent.getStringExtra("threadId"))
            .put("turnId", intent.getStringExtra("turnId"))
            .put("requestId", intent.getStringExtra("requestId"))
            .put("callId", intent.getStringExtra("callId"))
            .put("decision", "accept")
        // Out of the page's id range; the Host's answer has nobody to go to.
        Relay.invoke(NOTIFICATION_CALL_ID, "call", JSONArray().put("approval/respond").put(params))
        Notifier.cancel(context, intent.getIntExtra("notificationId", 0))
    }

    private companion object {
        const val NOTIFICATION_CALL_ID = 2_000_000_000
    }
}
