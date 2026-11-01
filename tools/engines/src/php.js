import uniter from 'uniter';
globalThis.LF_PHP = (code, write) => {
  const php = uniter.createEngine('PHP');
  php.getStdout().on('data', write); php.getStderr().on('data', write);
  if (!/^\s*<\?php/.test(code)) code = '<?php\n' + code;
  return php.execute(code);
};
