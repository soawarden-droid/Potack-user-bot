package dev.xa.potack;

import android.app.*;
import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.IBinder;
import com.chaquo.python.Python;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

public class BotService extends Service {
    // One queue across service recreation: finish old disconnects before a new login.
    private static final ExecutorService worker = Executors.newSingleThreadExecutor();
    public static volatile String serviceError = "";
    private static final String CHANNEL = "xa_listener";

    @Override public void onCreate() {
        super.onCreate();
        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "xa userbot", NotificationManager.IMPORTANCE_LOW));
    }

    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (intent == null) { stopSelf(id); return START_NOT_STICKY; }
        String action = intent.getStringExtra("action");
        String payload = intent.getStringExtra("payload");
        startForeground(1, notification("Connecting · open xa for status"));
        if ("stop".equals(action)) {
            try { Python.getInstance().getModule("android_bridge").callAttr("request_stop"); }
            catch (Exception ignored) { /* The queued stop still disconnects the runtime. */ }
        }
        worker.execute(() -> {
            try {
                serviceError = "";
                String result = Python.getInstance().getModule("android_bridge").callAttr("dispatch",
                    getFilesDir().getAbsolutePath(), action, payload == null ? "{}" : payload).toString();
                JSONObject state = new JSONObject(result);
                if (Build.VERSION.SDK_INT < 33 || checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
                    getSystemService(NotificationManager.class).notify(1, notification(state.getString("message")));
                }
                // Login waits stay alive so code delivery survives switching to Telegram.
                String phase = state.getString("phase");
                if ("offline".equals(phase) || ("error".equals(phase) && !state.getBoolean("running"))) {
                    stopSelf(id);
                }
            } catch (Exception exception) {
                serviceError = "The userbot could not start. Stop it and retry.";
                stopSelf(id);
            }
        });
        return START_NOT_STICKY;
    }

    private Notification notification(String message) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Intent stopIntent = new Intent(this, BotService.class).putExtra("action", "stop");
        PendingIntent stop = PendingIntent.getService(this, 1, stopIntent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("xa · Potack").setContentText(message).setContentIntent(open)
            .addAction(new Notification.Action.Builder(null, "Stop", stop).build()).setOngoing(true).build();
    }

    @Override public void onDestroy() {
        worker.execute(() -> {
            try { Python.getInstance().getModule("android_bridge").callAttr("dispatch", getFilesDir().getAbsolutePath(), "shutdown", "{}"); }
            catch (Exception ignored) { }
        });
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
