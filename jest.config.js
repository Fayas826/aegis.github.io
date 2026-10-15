/** @type {import('ts-jest').JestConfigWithTsJest} */
module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  // Keep the root Node service isolated from nested worktrees and frontend
  // packages.  Otherwise Jest reports duplicate package names and can resolve
  // the unrelated src/index.jsx before the TypeScript backend entry point.
  roots: ['<rootDir>/src', '<rootDir>/tests'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json', 'node'],
  testPathIgnorePatterns: ['/node_modules/', '/\.jarvis_worktrees/'],
  testMatch: ['**/**/*.test.ts'],
  verbose: true,
  forceExit: true,
  clearMocks: true,
};
