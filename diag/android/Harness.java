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
        for (String t : turns) {
            msgs.add(new String[]{"user", t});
            String cached = gen(shared, msgs);
            long fresh = LlamaEngine.nativeLoad(model, 4096, 4);
            String clean = gen(fresh, msgs);
            LlamaEngine.nativeFree(fresh);
            System.out.println("\n### USER: " + t);
            System.out.println("  app engine (cache reused): " + cached.replace("\n", " ").substring(0, Math.min(300, cached.length())));
            System.out.println("  fresh engine (no cache)  : " + clean.replace("\n", " ").substring(0, Math.min(300, clean.length())));
            System.out.println("  SAME: " + cached.equals(clean));
            msgs.add(new String[]{"assistant", cached});
        }
    }
}
