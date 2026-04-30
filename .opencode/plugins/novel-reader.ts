import path from "node:path";
import { tool, type Plugin } from "@opencode-ai/plugin";

async function runNovelReader(root: string, args: string[]) {
  const env = {
    ...Bun.env,
    PYTHONPATH: [path.join(root, "src"), Bun.env.PYTHONPATH ?? ""]
      .filter(Boolean)
      .join(path.delimiter),
  };
  const proc = Bun.spawn(["python", "-m", "novel_reader.cli", ...args], {
    cwd: root,
    env,
    stdout: "pipe",
    stderr: "pipe",
  });
  const stdout = await new Response(proc.stdout).text();
  const stderr = await new Response(proc.stderr).text();
  const code = await proc.exited;
  if (code !== 0) {
    throw new Error(stderr || stdout || `novel-reader exited with ${code}`);
  }
  return stdout.trim();
}

export const NovelReaderPlugin: Plugin = async ({ directory }) => {
  const root = directory;

  return {
    tool: {
      novel_status: tool({
        description: "Show novel reading coverage and local index status.",
        args: {
          book: tool.schema.string().describe("The book_id returned by novel-reader ingest."),
        },
        async execute(args) {
          return runNovelReader(root, ["status", args.book, "--json"]);
        },
      }),
      novel_read: tool({
        description: "Read one chapter or chunk from the indexed novel.",
        args: {
          book: tool.schema.string().describe("The book_id returned by novel-reader ingest."),
          chapter: tool.schema.number().optional().describe("Chapter number to read."),
          chunk: tool.schema.string().optional().describe("Chunk id such as c0001-001."),
        },
        async execute(args) {
          const cliArgs = ["read", args.book, "--json"];
          if (args.chunk) cliArgs.push("--chunk", args.chunk);
          else if (args.chapter) cliArgs.push("--chapter", String(args.chapter));
          else throw new Error("Provide either chapter or chunk.");
          return runNovelReader(root, cliArgs);
        },
      }),
      novel_search: tool({
        description: "Search indexed novel text and return source-grounded snippets.",
        args: {
          book: tool.schema.string().describe("The book_id returned by novel-reader ingest."),
          query: tool.schema.string().describe("Search query or plot question."),
          top: tool.schema.number().optional().describe("Maximum snippets to return."),
          semantic: tool.schema.boolean().optional().describe("Use optional embedding index when available."),
        },
        async execute(args) {
          const cliArgs = ["search", args.book, args.query, "--json"];
          if (args.top) cliArgs.push("--top", String(args.top));
          if (args.semantic) cliArgs.push("--semantic");
          return runNovelReader(root, cliArgs);
        },
      }),
      novel_style: tool({
        description: "Return language style distillation evidence for original-writing guidance.",
        args: {
          book: tool.schema.string().describe("The book_id returned by novel-reader ingest."),
          scene: tool.schema.string().optional().describe("Optional scene type: 战斗, 悬疑, 感情, 日常, or 说明."),
        },
        async execute(args) {
          const cliArgs = ["style", args.book, "--json"];
          if (args.scene) cliArgs.push("--scene", args.scene);
          return runNovelReader(root, cliArgs);
        },
      }),
      novel_continue: tool({
        description: "Build a source-grounded continuation writing package.",
        args: {
          book: tool.schema.string().describe("The book_id returned by novel-reader ingest."),
          afterChapter: tool.schema.number().optional().describe("Continue after this chapter."),
          afterChunk: tool.schema.string().optional().describe("Continue after this chunk id."),
          outline: tool.schema.string().optional().describe("Optional user-provided continuation outline."),
          outlineFile: tool.schema.string().optional().describe("Optional UTF-8 outline file path."),
          semantic: tool.schema.boolean().optional().describe("Use semantic search when embedding is available."),
          scene: tool.schema.string().optional().describe("Optional scene type: 战斗, 悬疑, 感情, 日常, or 说明."),
          length: tool.schema.string().optional().describe("Target length: short, medium, or long."),
        },
        async execute(args) {
          const cliArgs = ["continue", args.book, "--json"];
          if (args.afterChapter) cliArgs.push("--after-chapter", String(args.afterChapter));
          if (args.afterChunk) cliArgs.push("--after-chunk", args.afterChunk);
          if (args.outline) cliArgs.push("--outline", args.outline);
          if (args.outlineFile) cliArgs.push("--outline-file", args.outlineFile);
          if (args.semantic) cliArgs.push("--semantic");
          if (args.scene) cliArgs.push("--scene", args.scene);
          if (args.length) cliArgs.push("--length", args.length);
          return runNovelReader(root, cliArgs);
        },
      }),
    },
  };
};
