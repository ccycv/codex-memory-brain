import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { tool } from "@opencode-ai/plugin";

const STATUSES = [
  "active",
  "paused",
  "blocked",
  "usage_limited",
  "budget_limited",
  "complete",
];

function goalHome() {
  return process.env.OPENCODE_GOAL_HOME || path.join(os.homedir(), ".local", "share", "opencode-goal");
}

function storePath() {
  return path.join(goalHome(), "goals.json");
}

function nowIso() {
  return new Date().toISOString();
}

function emptyStore() {
  return {
    version: 1,
    sessions: {},
  };
}

async function readStore() {
  const file = storePath();
  try {
    const text = await fs.promises.readFile(file, "utf8");
    const data = JSON.parse(text);
    if (!data || typeof data !== "object" || !data.sessions || typeof data.sessions !== "object") {
      return emptyStore();
    }
    return data;
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return emptyStore();
    }
    throw error;
  }
}

async function writeStore(data) {
  const file = storePath();
  await fs.promises.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.tmp`;
  await fs.promises.writeFile(tmp, `${JSON.stringify(data, null, 2)}\n`);
  await fs.promises.rename(tmp, file);
}

function visible(goal) {
  if (!goal) {
    return "Goal: no active goal for this session.";
  }
  return `Goal: ${goal.status} - ${goal.objective}`;
}

function publicGoal(goal) {
  if (!goal) {
    return null;
  }
  return {
    session_id: goal.session_id,
    project: goal.project,
    directory: goal.directory,
    worktree: goal.worktree,
    objective: goal.objective,
    status: goal.status,
    notes: goal.notes || "",
    progress: goal.progress || "",
    next_steps: goal.next_steps || [],
    blockers: goal.blockers || [],
    token_budget: goal.token_budget ?? null,
    tokens_used: goal.tokens_used || 0,
    cost_usd: goal.cost_usd || 0,
    time_used_seconds: goal.time_used_seconds || 0,
    created_at: goal.created_at,
    updated_at: goal.updated_at,
    completed_at: goal.completed_at || null,
    history: goal.history || [],
  };
}

function getSessionGoal(store, sessionID) {
  const goal = store.sessions[sessionID];
  if (!goal || goal.archived) {
    return null;
  }
  return goal;
}

function addHistory(goal, event, data = {}) {
  goal.history = Array.isArray(goal.history) ? goal.history : [];
  goal.history.push({
    at: nowIso(),
    event,
    ...data,
  });
  if (goal.history.length > 50) {
    goal.history = goal.history.slice(-50);
  }
}

async function mutateGoal(sessionID, mutator) {
  const store = await readStore();
  const result = await mutator(store);
  await writeStore(store);
  return result;
}

function newGoal(args, context) {
  const created = nowIso();
  return {
    session_id: context.sessionID,
    project: "",
    directory: context.directory,
    worktree: context.worktree,
    objective: args.objective.trim(),
    status: "active",
    notes: args.notes || "",
    progress: "",
    next_steps: [],
    blockers: [],
    token_budget: args.token_budget ?? null,
    tokens_used: 0,
    cost_usd: 0,
    time_used_seconds: 0,
    created_at: created,
    updated_at: created,
    completed_at: null,
    archived: false,
    history: [],
  };
}

function parseList(value) {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean).slice(0, 20);
  }
  return String(value)
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 20);
}

function goalMarkdown(goal) {
  if (!goal) {
    return "No goal is set for this OpenCode session.";
  }
  const lines = [
    `Status: ${goal.status}`,
    `Objective: ${goal.objective}`,
    `Tokens: ${goal.tokens_used || 0}${goal.token_budget ? ` / ${goal.token_budget}` : ""}`,
    `Time: ${Math.round(goal.time_used_seconds || 0)}s`,
  ];
  if (goal.progress) {
    lines.push(`Progress: ${goal.progress}`);
  }
  if (goal.blockers?.length) {
    lines.push(`Blockers: ${goal.blockers.join("; ")}`);
  }
  if (goal.next_steps?.length) {
    lines.push(`Next steps: ${goal.next_steps.join("; ")}`);
  }
  return lines.join("\n");
}

const stepStarts = new Map();

export const OpenCodeGoalPlugin = async () => {
  return {
    tool: {
      goal_set: tool({
        description: "Set or replace the active goal for the current OpenCode session.",
        args: {
          objective: tool.schema.string().min(1).max(4000).describe("The goal objective for this session."),
          notes: tool.schema.string().max(4000).optional().describe("Optional notes or acceptance criteria."),
          token_budget: tool.schema.number().int().positive().optional().describe("Optional token budget for the goal."),
        },
        async execute(args, context) {
          const goal = await mutateGoal(context.sessionID, async (store) => {
            const next = newGoal(args, context);
            addHistory(next, "set", { objective: next.objective });
            store.sessions[context.sessionID] = next;
            return next;
          });
          return {
            title: visible(goal),
            output: JSON.stringify({ visible_status: visible(goal), goal: publicGoal(goal) }, null, 2),
            metadata: { goal: publicGoal(goal) },
          };
        },
      }),
      goal_status: tool({
        description: "Show the active goal for the current OpenCode session.",
        args: {
          include_history: tool.schema.boolean().optional().describe("Include recent status history."),
        },
        async execute(args, context) {
          const store = await readStore();
          const goal = getSessionGoal(store, context.sessionID);
          const payload = publicGoal(goal);
          if (payload && !args.include_history) {
            payload.history = [];
          }
          return {
            title: visible(goal),
            output: JSON.stringify({ visible_status: visible(goal), summary: goalMarkdown(goal), goal: payload }, null, 2),
            metadata: { goal: payload },
          };
        },
      }),
      goal_update: tool({
        description: "Update objective, status, progress, blockers, next steps, or token budget for the current goal.",
        args: {
          status: tool.schema.enum(STATUSES).optional(),
          objective: tool.schema.string().min(1).max(4000).optional(),
          progress: tool.schema.string().max(4000).optional(),
          blockers: tool.schema.array(tool.schema.string().max(1000)).max(20).optional(),
          next_steps: tool.schema.array(tool.schema.string().max(1000)).max(20).optional(),
          token_budget: tool.schema.number().int().positive().optional(),
        },
        async execute(args, context) {
          const goal = await mutateGoal(context.sessionID, async (store) => {
            const current = getSessionGoal(store, context.sessionID);
            if (!current) {
              throw new Error("No goal is set for this session. Call goal_set first.");
            }
            if (args.status) current.status = args.status;
            if (args.objective) current.objective = args.objective.trim();
            if (args.progress !== undefined) current.progress = args.progress;
            if (args.blockers !== undefined) current.blockers = parseList(args.blockers);
            if (args.next_steps !== undefined) current.next_steps = parseList(args.next_steps);
            if (args.token_budget !== undefined) current.token_budget = args.token_budget;
            current.updated_at = nowIso();
            if (current.status === "complete" && !current.completed_at) current.completed_at = current.updated_at;
            addHistory(current, "update", {
              status: current.status,
              progress: args.progress,
            });
            return current;
          });
          return {
            title: visible(goal),
            output: JSON.stringify({ visible_status: visible(goal), goal: publicGoal(goal) }, null, 2),
            metadata: { goal: publicGoal(goal) },
          };
        },
      }),
      goal_complete: tool({
        description: "Mark the current goal complete with an optional completion summary.",
        args: {
          summary: tool.schema.string().max(4000).optional(),
        },
        async execute(args, context) {
          const goal = await mutateGoal(context.sessionID, async (store) => {
            const current = getSessionGoal(store, context.sessionID);
            if (!current) {
              throw new Error("No goal is set for this session. Call goal_set first.");
            }
            current.status = "complete";
            current.progress = args.summary || current.progress || "Completed.";
            current.completed_at = nowIso();
            current.updated_at = current.completed_at;
            addHistory(current, "complete", { summary: args.summary || "" });
            return current;
          });
          return {
            title: visible(goal),
            output: JSON.stringify({ visible_status: visible(goal), goal: publicGoal(goal) }, null, 2),
            metadata: { goal: publicGoal(goal) },
          };
        },
      }),
      goal_clear: tool({
        description: "Archive the current goal for this session without deleting goal history.",
        args: {
          reason: tool.schema.string().max(1000).optional(),
        },
        async execute(args, context) {
          const goal = await mutateGoal(context.sessionID, async (store) => {
            const current = getSessionGoal(store, context.sessionID);
            if (!current) {
              return null;
            }
            current.archived = true;
            current.updated_at = nowIso();
            addHistory(current, "clear", { reason: args.reason || "" });
            return current;
          });
          return {
            title: "Goal: cleared.",
            output: JSON.stringify({ visible_status: "Goal: cleared.", goal: publicGoal(goal) }, null, 2),
            metadata: { goal: publicGoal(goal) },
          };
        },
      }),
      goal_list: tool({
        description: "List recent OpenCode goals stored locally.",
        args: {
          limit: tool.schema.number().int().positive().max(50).optional(),
          include_archived: tool.schema.boolean().optional(),
        },
        async execute(args) {
          const store = await readStore();
          const limit = args.limit || 10;
          const goals = Object.values(store.sessions)
            .filter((goal) => args.include_archived || !goal.archived)
            .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))
            .slice(0, limit)
            .map(publicGoal);
          return {
            title: `Goal: listed ${goals.length} goals.`,
            output: JSON.stringify({ visible_status: `Goal: listed ${goals.length} goals.`, goals }, null, 2),
            metadata: { goals },
          };
        },
      }),
    },
    event: async ({ event }) => {
      if (event.type === "session.next.step.started") {
        stepStarts.set(event.properties.sessionID, event.properties.timestamp);
        return;
      }
      if (event.type !== "session.next.step.ended") {
        return;
      }
      const sessionID = event.properties.sessionID;
      const start = stepStarts.get(sessionID);
      stepStarts.delete(sessionID);
      const tokens = event.properties.tokens || {};
      const cache = tokens.cache || {};
      const totalTokens = Number(tokens.input || 0) + Number(tokens.output || 0) + Number(tokens.reasoning || 0) + Number(cache.read || 0) + Number(cache.write || 0);
      const duration = start ? Math.max(0, (Number(event.properties.timestamp) - Number(start)) / 1000) : 0;
      await mutateGoal(sessionID, async (store) => {
        const goal = getSessionGoal(store, sessionID);
        if (!goal || goal.status === "complete") {
          return null;
        }
        goal.tokens_used = Number(goal.tokens_used || 0) + totalTokens;
        goal.cost_usd = Number(goal.cost_usd || 0) + Number(event.properties.cost || 0);
        goal.time_used_seconds = Number(goal.time_used_seconds || 0) + duration;
        if (goal.token_budget && goal.tokens_used >= goal.token_budget && goal.status === "active") {
          goal.status = "budget_limited";
          addHistory(goal, "budget_limited", { tokens_used: goal.tokens_used, token_budget: goal.token_budget });
        }
        goal.updated_at = nowIso();
        return goal;
      });
    },
    "experimental.chat.system.transform": async (input, output) => {
      const store = await readStore();
      const goal = input.sessionID ? getSessionGoal(store, input.sessionID) : null;
      if (!goal || goal.status === "complete") {
        return;
      }
      output.system.push(`## Active OpenCode Goal
Status: ${goal.status}
Objective: ${goal.objective}
Progress: ${goal.progress || "No progress recorded yet."}
Blockers: ${(goal.blockers || []).join("; ") || "None recorded."}
Next steps: ${(goal.next_steps || []).join("; ") || "None recorded."}
Tokens used: ${goal.tokens_used || 0}${goal.token_budget ? ` / ${goal.token_budget}` : ""}

Keep the work aligned with this goal. If the goal changes, call goal_update. If the goal is complete, call goal_complete. If blocked, call goal_update with status="blocked" and concise blockers.`);
    },
    "experimental.session.compacting": async (input, output) => {
      const store = await readStore();
      const goal = getSessionGoal(store, input.sessionID);
      if (!goal || goal.status === "complete") {
        return;
      }
      output.context.push(`## Active OpenCode Goal
${goalMarkdown(goal)}
Preserve this goal and its current status in the continuation summary.`);
    },
  };
};
