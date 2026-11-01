globalThis.LF_RUBY = (code, write) => {
  const O = globalThis.Opal;
  if (!globalThis.__lfOpalReady) { O.require('opal-builder'); globalThis.__lfOpalReady = true; }
  O.gvars.stdout.write_proc = (s) => { write(s); };
  O.gvars.stderr.write_proc = (s) => { write(s); };
  const js = O.Opal.$compile(code);
  (new Function(js))();
};
