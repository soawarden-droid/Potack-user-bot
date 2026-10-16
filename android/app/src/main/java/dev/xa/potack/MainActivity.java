package dev.xa.potack;

import android.Manifest;
import android.app.Activity;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.*;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import com.chaquo.python.Python;
import org.json.JSONObject;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    private static final int BG = Color.rgb(9,13,11), PANEL = Color.rgb(17,25,20), GREEN = Color.rgb(164,255,94);
    private static final int MUTED = Color.rgb(143,164,149), WHITE = Color.rgb(232,241,234);
    private LinearLayout content, setup, codePanel, passwordPanel;
    private TextView status, detail;
    private EditText apiId, apiHash, phone, aiKey, searchUrl, code, password;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService reader = Executors.newSingleThreadExecutor();
    private final java.util.List<Button> controls = new java.util.ArrayList<>();
    private boolean visible, reading;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true); scroll.setBackgroundColor(BG);
        scroll.setClipToPadding(false);
        scroll.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets;
        });
        content = column(); content.setPadding(dp(24),dp(24),dp(24),dp(32)); scroll.addView(content);
        setContentView(scroll);
        label(content,"PERSONAL TELEGRAM SYSTEM / 01",11,MUTED);
        label(content,"xa_",76,GREEN).setContentDescription("xa · Potack");
        label(content,"YOUR ACCOUNT. YOUR COMMANDS.",12,WHITE);
        label(content,"POTACK  /  ANDROID",11,MUTED);
        LinearLayout card = column(); card.setPadding(dp(18),dp(18),dp(18),dp(18)); card.setBackground(box(PANEL, 14));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(-1,-2); cardParams.topMargin=dp(28); content.addView(card,cardParams);
        status=label(card,"● OFFLINE",17,GREEN);
        detail=label(card,"Ready when you are.",13,WHITE); detail.setPadding(0,dp(10),0,dp(14));
        button(card,"START USERBOT", () -> send("start",new JSONObject()),true);
        button(card,"STOP", () -> send("stop",new JSONObject()),false);
        button(card,"SIGN OUT OF TELEGRAM", () -> send("logout",new JSONObject()),false);
        button(content,"ACCOUNT SETUP  +", () -> setup.setVisibility(setup.getVisibility()==View.VISIBLE?View.GONE:View.VISIBLE),false);
        setup=column();content.addView(setup);
        label(setup,"Sign in with your own Telegram account.",13,WHITE);
        label(setup,"Get API ID and hash from my.telegram.org. Your login stays in this app’s private storage.",12,MUTED);
        button(setup,"OPEN TELEGRAM API SETTINGS ↗", () -> startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse("https://my.telegram.org/apps"))),false);
        apiId=input(setup,"Telegram API ID",InputType.TYPE_CLASS_NUMBER);
        apiHash=input(setup,"Telegram API hash",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        phone=input(setup,"Phone number (+country code)",InputType.TYPE_CLASS_PHONE);
        aiKey=input(setup,"OpenRouter key (optional)",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        searchUrl=input(setup,"HTTPS SearXNG URL (optional)",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
        button(setup,"CONNECT / SEND LOGIN CODE", () -> {
            JSONObject values=new JSONObject();
            try { values.put("api_id",text(apiId));values.put("api_hash",text(apiHash));values.put("phone",text(phone));
                values.put("ai_key",text(aiKey));values.put("search_url",text(searchUrl)); }
            catch(Exception ignored) { return; }
            send("login",values);apiHash.setText("");aiKey.setText("");
        },true);
        codePanel=column();content.addView(codePanel);codePanel.setVisibility(View.GONE);
        code=input(codePanel,"Telegram login code",InputType.TYPE_CLASS_NUMBER);
        button(codePanel,"VERIFY CODE", () -> { sendValue("code","code",text(code));code.setText(""); },true);
        passwordPanel=column();content.addView(passwordPanel);passwordPanel.setVisibility(View.GONE);
        password=input(passwordPanel,"Telegram two-step password",InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_PASSWORD);
        button(passwordPanel,"UNLOCK", () -> { sendValue("password","password",password.getText().toString());password.setText(""); },true);
        label(content,"\nCOMMAND INDEX",12,GREEN);
        label(content,"Send these in Telegram after starting xa.",12,MUTED);
        label(content,".help       All commands\n.ping       Connection check\n/gifthelp   Gifts & Stars\n/gifts      Gift catalog\n/mygifts    Your gifts\n.ai text    AI assistant¹\n.save k v   Save a note\nQalawa preview 10   Preview cleanup",13,WHITE).setLineSpacing(dp(8),1);
        label(content,"¹ Requires your OpenRouter key. Search requires your own HTTPS SearXNG server.\n\nKeep xa running for commands and gift watches. Android battery controls may stop it; check your phone’s battery settings if it disconnects.\n\nCreated by xa  ·  v0.1.1",11,MUTED);
        // Restore only public fields. Secrets and one-time login values are never view-state saved.
        android.content.SharedPreferences prefs=getPreferences(MODE_PRIVATE);
        apiId.setText(prefs.getString("api_id",""));phone.setText(prefs.getString("phone",""));
        setup.setVisibility(new java.io.File(getFilesDir(),"potack_private.json").exists()?View.GONE:View.VISIBLE);
        if (Build.VERSION.SDK_INT>=33) requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},7);
    }

    private void sendValue(String action,String key,String value) {
        try { JSONObject data=new JSONObject();data.put(key,value);send(action,data); } catch(Exception ignored) { }
    }
    private void send(String action,JSONObject values) {
        getPreferences(MODE_PRIVATE).edit().putString("api_id",text(apiId)).putString("phone",text(phone)).apply();
        detail.setText(R.string.working);
        try { startForegroundService(new Intent(this,BotService.class).putExtra("action",action).putExtra("payload",values.toString())); }
        catch(Exception error) { detail.setText(R.string.service_error); }
    }
    private final Runnable refresh=new Runnable() {
        @Override public void run() {
            if (!visible) return;
            if (!reading) { reading=true;reader.execute(() -> {
                String result;
                try { result=Python.getInstance().getModule("android_bridge").callAttr("snapshot").toString(); }
                catch(Exception error) { result="{\"phase\":\"error\",\"message\":\"Runtime unavailable. Reopen xa.\"}"; }
                final String state=result;
                handler.post(() -> { reading=false;if(visible) show(state); });
            }); }
            handler.postDelayed(this,1000);
        }
    };
    private void show(String raw) {
        try {
            JSONObject state=new JSONObject(raw);String phase=state.getString("phase");
            boolean busy=state.optBoolean("busy",false);
            status.setText(getString(R.string.status_format,phase.toUpperCase(java.util.Locale.ROOT)));
            detail.setText(BotService.serviceError.isEmpty()?(busy?getString(R.string.working):state.getString("message")):BotService.serviceError);
            for(Button control:controls) {
                boolean enabled=!busy||control.getText().toString().equals("STOP");
                control.setEnabled(enabled);control.setAlpha(enabled?1f:0.45f);
            }
            codePanel.setVisibility(phase.equals("code")?View.VISIBLE:View.GONE);
            passwordPanel.setVisibility(phase.equals("password")?View.VISIBLE:View.GONE);
            if(phase.equals("ready")||phase.equals("online")||phase.equals("code")||phase.equals("password")) setup.setVisibility(View.GONE);
            if(state.getString("message").startsWith("Signed out.")) setup.setVisibility(View.VISIBLE);
        } catch(Exception ignored) { }
    }
    @Override public void onResume() { super.onResume();visible=true;handler.post(refresh); }
    @Override public void onPause() { visible=false;handler.removeCallbacks(refresh);super.onPause(); }
    @Override public void onDestroy() { reader.shutdown();super.onDestroy(); }
    private String text(EditText field) {return field.getText().toString().trim();}
    private int dp(int n) {return (int)(n*getResources().getDisplayMetrics().density);}
    private LinearLayout column() {LinearLayout view=new LinearLayout(this);view.setOrientation(LinearLayout.VERTICAL);return view;}
    private GradientDrawable box(int color,int radius) {GradientDrawable d=new GradientDrawable();d.setColor(color);d.setCornerRadius(dp(radius));return d;}
    private TextView label(LinearLayout parent,String text,int size,int color) {
        TextView v=new TextView(this);v.setText(text);v.setTextSize(size);v.setTextColor(color);v.setTypeface(Typeface.MONOSPACE);
        v.setPadding(0,dp(6),0,dp(6));parent.addView(v,new LinearLayout.LayoutParams(-1,-2));return v;
    }
    private EditText input(LinearLayout parent,String hint,int type) {
        EditText v=new EditText(this);v.setHint(hint);v.setTextColor(WHITE);v.setHintTextColor(MUTED);v.setTextSize(14);
        v.setSingleLine(true);v.setInputType(type);v.setSaveEnabled(false);v.setPadding(dp(12),dp(12),dp(12),dp(12));
        v.setBackground(box(PANEL,6));LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(54));p.topMargin=dp(8);parent.addView(v,p);return v;
    }
    private void button(LinearLayout parent,String text,Runnable action,boolean primary) {
        Button v=new Button(this);v.setText(text);v.setTextSize(12);v.setTypeface(Typeface.MONOSPACE,Typeface.BOLD);
        controls.add(v);
        v.setTextColor(primary?BG:GREEN);v.setBackground(box(primary?GREEN:PANEL,6));v.setOnClickListener(view -> action.run());
        LinearLayout.LayoutParams p=new LinearLayout.LayoutParams(-1,dp(50));p.topMargin=dp(10);parent.addView(v,p);
    }
}
