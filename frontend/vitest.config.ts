import {defineConfig} from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins:[react()],
  cacheDir:process.env.TEMP?`${process.env.TEMP}/enrui-lineage-vitest`:undefined,
  test:{environment:'jsdom',include:['tests/**/*.test.tsx'],restoreMocks:true},
});
