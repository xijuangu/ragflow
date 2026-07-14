import type { Config } from 'jest';

const config: Config = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest-setup.ts'],
  transform: {
    '^.+\\.[jt]sx?$': '<rootDir>/jest-esbuild-transformer.cjs',
  },
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '^@parent/(.*)$': '<rootDir>/../src/$1',
    '\\.(css|less)$': '<rootDir>/jest-style-mock.cjs',
    '\\.(gif|jpg|jpeg|png|svg|webp)$': '<rootDir>/jest-file-mock.cjs',
  },
  collectCoverageFrom: [
    'src/**/*.{ts,tsx,js,jsx}',
    '!src/**/*.d.ts',
    '!coverage/**',
    '!dist/**',
  ],
  coverageThreshold: {
    global: {
      lines: 1,
    },
  },
};

export default config;
