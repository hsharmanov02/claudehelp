// Secret JSON is passed only through local pipes. Never log keys or phrases.
const { Wallet } = require('ethers');
const fs = require('node:fs');

async function main() {
  const command = process.argv[2];
  if (command === 'create') {
    const wallet = Wallet.createRandom();
    process.stdout.write(JSON.stringify({
      version: 1,
      createdAt: new Date().toISOString(),
      address: wallet.address,
      privateKey: wallet.privateKey,
      mnemonic: wallet.mnemonic.phrase,
      derivationPath: wallet.path,
      purpose: 'income-node-base-receiver',
    }));
    return;
  }
  const payload = JSON.parse(fs.readFileSync(0, 'utf8').replace(/^\uFEFF/, ''));
  const record = payload.wallet ?? payload;
  const wallet = new Wallet(record.privateKey);
  const recovered = Wallet.fromPhrase(record.mnemonic);
  if (wallet.address !== record.address || recovered.privateKey !== wallet.privateKey) {
    throw new Error('Local wallet validation failed');
  }
  if (command === 'inspect') {
    process.stdout.write(JSON.stringify({ address: wallet.address, recoveryVerified: true }));
    return;
  }
  if (command === 'backup') {
    if (typeof payload.password !== 'string' || payload.password.length < 16) {
      throw new Error('Use a backup passphrase of at least 16 characters');
    }
    // An interoperable Web3 Secret Storage file; only encrypted data leaves stdout.
    process.stdout.write(await recovered.encrypt(payload.password));
    return;
  }
  throw new Error('Unknown wallet operation');
}
main().catch(() => {
  process.stderr.write('Wallet operation failed; sensitive details suppressed.\n');
  process.exitCode = 1;
});
