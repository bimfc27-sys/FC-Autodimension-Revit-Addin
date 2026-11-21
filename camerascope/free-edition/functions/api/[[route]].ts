// Pages Functions entry: every /api/* request is handled by the Hono app.
import { app } from "../../src/app";
import type { Env } from "../../src/core";

export const onRequest: PagesFunction<Env> = (ctx) => app.fetch(ctx.request, ctx.env, ctx as unknown as ExecutionContext);
