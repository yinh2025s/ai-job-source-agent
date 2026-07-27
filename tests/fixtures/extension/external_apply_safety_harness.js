const fs = require("node:fs");
const vm = require("node:vm");

const sandbox = { URL };
sandbox.globalThis = sandbox;
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), sandbox, { filename: process.argv[2] });

const values = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(values.map((value) => (
  sandbox.JobSourceExternalApplySafety.sanitize(value, "https://www.linkedin.com/jobs/view/123")
))));
