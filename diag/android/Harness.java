import com.localforge.app.engine.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;

/** Replays a multi-turn chat through the Android app's real native engine (KV cache reuse) and a fresh engine. */
public class Harness {
    static byte[] u(String s) { return s.getBytes(StandardCharsets.UTF_8); }
    static String gen(long h, List<String[]> msgs) {
        String[] roles = msgs.stream().map(m -> m[0]).toArray(String[]::new);
        byte[][] c = msgs.stream().map(m -> u(m[1])).toArray(byte[][]::new);
        String text = new String(LlamaEngine.nativeFormatChat(h, roles, c), StandardCharsets.UTF_8);
        if (text.endsWith("<|im_start|>assistant\n")) text += "<think>\n\n</think>\n\n";
        StringBuilder out = new StringBuilder();
        int rc = LlamaEngine.nativeGenerate(h, u(text), 160, 0.0f, b -> { out.append(new String(b, StandardCharsets.UTF_8)); return true; });
        return (rc != 0 ? "(rc=" + rc + ") " : "") + out.toString().replaceAll("(?s)<think>.*?</think>", "").trim();
    }
    public static void main(String[] a) throws Exception {
        String model = a[0];
        String sys = Files.readString(Path.of(a[1]));
        LlamaEngine.nativeInit("");
        long shared = LlamaEngine.nativeLoad(model, 4096, 4);
        List<String[]> msgs = new ArrayList<>();
        msgs.add(new String[]{"system", sys});
        String[] turns = {
            "My dog is called Biscuit and my favourite colour is teal. Remember that.",
            "Give me one fun fact about octopuses.",
            "What is the capital of Japan?",
            "What did I ask you just before this message? Quote it.",
            "What is my dog's name and my favourite colour?"
        };
        String v = a.length > 2 ? a[2] : "plain";
        if (!v.equals("plain")) msgs.set(0, new String[]{"system", sys + "\n- You remember this whole chat: the user's earlier messages and your replies are above. When the user refers to something earlier, use them. Never say you can't remember."});
        java.util.regex.Pattern back = java.util.regex.Pattern.compile("(?i)\\b(previous|last|earlier|before|prior|first)\\b.{0,30}\\b(prompt|question|message|request|ask|asked|said|typed|wrote)|what (did|was) i (just )?(ask|say|said|tell|type|write|want)|\\b(remind me what|you said|i said|i asked|i told you|i just asked)\\b");
        List<String> asked = new ArrayList<>();
        for (String t : turns) {
            String sent = t;
            if (v.equals("targeted") && !asked.isEmpty() && back.matcher(t).find()) {
                StringBuilder r = new StringBuilder("\n\n[Note from the app: my previous message, the one just before this one, was: \"" + asked.get(asked.size() - 1) + "\"");
                if (asked.size() > 1) {
                    r.append(". My earlier messages, oldest first: ");
                    for (int i = 0; i < asked.size() - 1; i++) r.append(i + 1).append(". \"").append(asked.get(i)).append("\" ");
                }
                sent = t + r + "]";
            }
            msgs.add(new String[]{"user", sent});
            String cached = gen(shared, msgs);
            System.out.println("\n### USER: " + t);
            System.out.println("  reply: " + cached.replace("\n", " ").substring(0, Math.min(300, cached.length())));
            msgs.set(msgs.size() - 1, new String[]{"user", t});
            msgs.add(new String[]{"assistant", cached});
            asked.add(t);
        }
    }
}
