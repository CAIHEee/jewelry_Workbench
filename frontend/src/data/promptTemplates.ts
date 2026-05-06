import rawPromptTemplates from "../../../shared/prompt_templates.json";

import type { PromptTemplate } from "../types/prompts";

export const promptTemplates: PromptTemplate[] = rawPromptTemplates as PromptTemplate[];

export function getPromptTemplatesByModule(module: PromptTemplate["module"]): PromptTemplate[] {
  return promptTemplates.filter((item) => item.module === module);
}
