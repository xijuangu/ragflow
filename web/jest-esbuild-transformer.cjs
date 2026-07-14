const { transformSync } = require('esbuild');

module.exports = {
  process(source, filename) {
    const extension = filename.split('.').pop();
    const loader =
      extension === 'tsx' || extension === 'jsx' ? extension : 'ts';
    const result = transformSync(source, {
      define: { 'import.meta.env': '{}' },
      format: 'cjs',
      jsx: 'automatic',
      loader,
      sourcemap: 'inline',
      target: 'es2022',
    });
    return { code: result.code };
  },
};
