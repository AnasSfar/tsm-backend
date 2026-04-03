const fs = require("node:fs");
const path = require("node:path");

const nodeModulesDir = path.join(process.cwd(), "node_modules");
const tsconfigPath = path.join(nodeModulesDir, "tsconfig.json");

try {
  if (!fs.existsSync(nodeModulesDir)) {
    process.exit(0);
  }

  if (fs.existsSync(tsconfigPath)) {
    process.exit(0);
  }

  const contents = JSON.stringify(
    {
      compilerOptions: {
        skipLibCheck: true,
      },
    },
    null,
    2,
  );

  fs.writeFileSync(tsconfigPath, contents + "\n", "utf8");
} catch (err) {
  // Don't fail installs because of this optional convenience.
  process.exit(0);
}
