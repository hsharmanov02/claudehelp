package com.localforge.app.engine;
public class LlamaEngine {
    static { System.loadLibrary("localforge"); }
    public static native int nativeInit(String dir);
    public static native long nativeLoad(String path, int nCtx, int nThreads);
    public static native void nativeFree(long h);
    public static native void nativeStop(long h);
    public static native int nativeCountTokens(long h, byte[] t);
    public static native int nativeCtxSize(long h);
    public static native byte[] nativeFormatChat(long h, String[] roles, byte[][] contents);
    public static native int nativeGenerate(long h, byte[] prompt, int maxTokens, float temp, TokenCallback cb);
}
