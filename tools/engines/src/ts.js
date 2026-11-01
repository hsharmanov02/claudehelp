import { transform } from 'sucrase';
globalThis.LF_TS = (code) => transform(code, { transforms: ['typescript'] }).code;
