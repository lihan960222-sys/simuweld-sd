import { defineConfig } from 'vite';

const moduleNames: Record<string,string> = {
  faceLoad: 'face_load', welding: 'weld_paths', heat: 'weld_paths', heatSettings: 'weld_paths', weldTimeline: 'weld_paths',
  thermal: 'thermal_cycle', localTrial: 'local_trial', inherentStrain:'inherent_strain',
};
const moduleIds = new Set(Object.values(moduleNames));
const outputDirectory = (name: string) => moduleIds.has(name) ? `modules/${name}` : 'shared';

export default defineConfig({
  server: {watch: {ignored: ['**/work/**', '**/release/**']}},
  build: {
    rollupOptions: {
      input: {
        index: 'index.html',
        face_load: 'frontend/faceLoad.ts', weld_paths: 'frontend/welding.ts',
        thermal_cycle: 'frontend/thermal.ts', local_trial: 'frontend/localTrial.ts',
        inherent_strain:'frontend/inherentStrain.ts',
      },
      preserveEntrySignatures: 'strict',
      output: {
        minifyInternalExports: false,
        entryFileNames: chunk => `${outputDirectory(chunk.name)}/${chunk.name}.js`,
        chunkFileNames: chunk => `${outputDirectory(chunk.name)}/${chunk.name}.js`,
        assetFileNames: asset => {
          const name = asset.names[0];
          const stem = name.replace(/\.[^.]+$/, '');
          return `${outputDirectory(stem)}/[name][extname]`;
        },
        manualChunks(id) {
          const path = id.replaceAll('\\','/');
          if(path.includes('/node_modules/'))return 'vendor';
          const name = path.split('/').pop()!.replace(/\.[^.]+$/, '');
          return moduleNames[name] || (path.includes('/frontend/') && name!=='main' ? 'common' : undefined);
        },
      },
    },
  },
});
