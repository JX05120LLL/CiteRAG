import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  { files: ['src/preview/**/*.ts', 'src/preview/**/*.tsx', 'src/react/**/*.ts', 'src/react/**/*.tsx',
    'src/features/voice/**/*.ts', 'src/api/client.ts'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    rules: { '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }] },
  },
);
