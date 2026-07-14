import '@testing-library/jest-dom';
import { TransformStream } from 'node:stream/web';
import { TextDecoder, TextEncoder } from 'node:util';
import 'whatwg-fetch';

Object.assign(globalThis, {
  TextDecoder,
  TextEncoder,
  TransformStream,
});
