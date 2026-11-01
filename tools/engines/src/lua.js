// fengari-web exposes a global `fengari` (bundled separately before this file)
const { lua, lauxlib, lualib, to_luastring } = globalThis.fengari;
globalThis.LF_LUA = (code, write) => {
  const L = lauxlib.luaL_newstate();
  lualib.luaL_openlibs(L);
  const join = (L, sep) => {
    const n = lua.lua_gettop(L); const parts = [];
    for (let i = 1; i <= n; i++) { lauxlib.luaL_tolstring(L, i); parts.push(lua.lua_tojsstring(L, -1)); lua.lua_pop(L, 1); }
    return parts.join(sep);
  };
  lua.lua_register(L, to_luastring('print'), (L) => { write(join(L, '\t') + '\n'); return 0; });
  // fengari-web has no io library; give scripts a minimal io.write
  if (lua.lua_getglobal(L, to_luastring('io')) !== lua.LUA_TTABLE) {
    lua.lua_pop(L, 1);
    lua.lua_newtable(L);
    lua.lua_pushvalue(L, -1);
    lua.lua_setglobal(L, to_luastring('io'));
  }
  lua.lua_pushcfunction(L, (L) => { write(join(L, '')); return 0; });
  lua.lua_setfield(L, -2, to_luastring('write'));
  lua.lua_pop(L, 1);
  if (lauxlib.luaL_loadstring(L, to_luastring(code)) !== 0 || lua.lua_pcall(L, 0, 0, 0) !== 0) {
    throw new Error(lua.lua_tojsstring(L, -1));
  }
};
