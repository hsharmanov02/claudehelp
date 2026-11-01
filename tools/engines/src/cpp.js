import JSCPP from 'JSCPP';
globalThis.LF_CPP = (code, write) => JSCPP.run(code, '', { stdio: { write } });
