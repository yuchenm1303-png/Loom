/* 对话 and 建造 are two separate places, each with its own list and its own last conversation. A conversation
 * lives in one of them: the Host records the mode of its latest turn (`weave`), and this machine remembers the
 * mode of one no turn has carried yet (a new build conversation, an older local choice). */
export type Space = "chat" | "project";

interface Listed { id: string; weave?: boolean; archived?: boolean; parentThreadId?: string | null; updatedAt?: string }

export function threadSpace(thread: { id: string; weave?: boolean }, projectIds: readonly string[], chatIds: readonly string[]): Space {
  return projectIds.includes(thread.id) || (Boolean(thread.weave) && !chatIds.includes(thread.id)) ? "project" : "chat";
}

/** Where switching into a space lands: the conversation last open there if it still can be, else its newest. */
export function landingThread<T extends Listed>(list: readonly T[], spaceOf: (thread: T) => Space, space: Space, lastId?: string): T | undefined {
  const here = list.filter((thread) => !thread.archived && spaceOf(thread) === space);
  const last = lastId ? here.find((thread) => thread.id === lastId) : undefined;
  if (last) return last;
  const time = (thread: T) => Date.parse(thread.updatedAt || "") || 0;
  return here.filter((thread) => !thread.parentThreadId).sort((a, b) => time(b) - time(a))[0];
}
