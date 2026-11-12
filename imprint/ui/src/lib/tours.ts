/** The dashboard's tutorials (ADR 0183): a few steps per page, each pointing
 * at a control of that page by its `data-tour` name. This file imports
 * nothing, so tests read it and check every step against its page. */
export type TourStep = {
  /** The `data-tour` name of the control the step points at; none: a step in the middle of the window. */
  target?: string;
  title: string;
  body: string;
  /** The step belongs to one dashboard only. */
  only?: "simple" | "advanced";
  /** The control depends on data or on the desktop app; the step is left out while it is absent. */
  optional?: boolean;
  /** The step also offers the simple or advanced choice. */
  choice?: "mode";
};

export type Tour = {
  /** Kept in this browser once the tour was finished or skipped; a rewritten tour takes a new id to be shown again. */
  id: string;
  /** The page it belongs to, as in lib/pages.ts; none for the welcome. */
  page?: string;
  steps: TourStep[];
};

export const WELCOME: Tour = {
  id: "welcome",
  steps: [
    {
      title: "Welcome to Imprint",
      body: "Imprint keeps what you and your AI tools work on, so you can find it, ask about it and pick it up again. Choose how much of the dashboard you want to see. You can change it at any time.",
      choice: "mode",
    },
    {
      target: "sidebar",
      title: "Your pages",
      body: "Every page is one click away here. Home shows what is in your memory, and Chat answers questions from it.",
      only: "simple",
    },
    {
      target: "sidebar",
      title: "Your pages",
      body: "Everyday pages come first. Memory, Activity and System hold the rest; a heading folds its group away.",
      only: "advanced",
    },
    {
      target: "mode-switch",
      title: "Simple or advanced",
      body: "Switch here whenever you want to see more, or less. Nothing is lost either way: both show the same memory and the same settings.",
    },
    {
      target: "help",
      title: "A short tutorial on every page",
      body: "The first time you open a page, a few steps like these show what it is for. This button shows them again, and turns tutorials off or on.",
    },
  ],
};

export const TOURS: Tour[] = [
  {
    id: "/", page: "/",
    steps: [
      { target: "page-header", only: "simple", title: "Home",
        body: "Home shows how much Imprint remembers, what was added lately, and anything that is waiting for you." },
      { target: "page-header", only: "advanced", title: "Dashboard",
        body: "One summary of the active workspace: its totals, what was added, what needs attention and what the memory is made of." },
      { target: "home-add", title: "Add to memory",
        body: "Pick what to add: a folder, a document, an audio file, a web page, or the conversations you had with your AI tools." },
      { target: "home-review", only: "simple", optional: true, title: "Needs your review",
        body: "When Imprint is not sure, it asks here: where something belongs, or whether something it noticed about you is right. One click answers, and every answer can be undone." },
      { target: "home-review", only: "advanced", optional: true, title: "Needs attention",
        body: "A job that failed in the last day, memories waiting for tagging, or a summary that could not be refreshed." },
      { target: "home-recent", optional: true, title: "Recently added",
        body: "The newest things in your memory. Click one to open its project." },
      { target: "activity", optional: true, title: "What Imprint is doing",
        body: "While Imprint reads something, this shows what it is and how far it has got." },
    ],
  },
  {
    id: "/chat", page: "/chat",
    steps: [
      { target: "chat-composer", title: "Ask about anything you added",
        body: "Write a question the way you would ask a colleague. Imprint looks through your memory and answers from what it finds." },
      { target: "chat-list", optional: true, title: "Your chats",
        body: "Earlier chats are kept here by date. Open one to carry on, or start a new one." },
      { target: "chat-echo", optional: true, title: "Carry on by voice",
        body: "Continue with Echo opens this chat as a spoken conversation. What is said there is added here." },
    ],
  },
  {
    id: "/search", page: "/search",
    steps: [
      { target: "search-box", title: "Search your memory",
        body: "Type a few words, a name or a file name and press Enter. Imprint matches the meaning as well as the exact words." },
      { target: "search-filters", only: "simple", title: "Narrow it down",
        body: "Type a project's name to search only in that project." },
      { target: "search-filters", only: "advanced", title: "Filters",
        body: "Limit a search to a project or a memory type, and choose the method: meaning plus exact terms, or meaning only." },
      { target: "search-results", title: "Results",
        body: "Click a result to read it beside the list. Open full memory shows all of it, and Read in context opens the conversation or file it came from." },
    ],
  },
  {
    id: "/voice", page: "/voice",
    steps: [
      { title: "Echo",
        body: "Echo is a voice you can talk to about your memory. It listens, thinks and speaks on this computer." },
      { target: "voice-start", optional: true, title: "Start talking",
        body: "Start a conversation, then hold the button while you speak, or switch to hands-free and just talk." },
      { target: "voice-settings", optional: true, title: "Companion settings",
        body: "Choose the microphone and the speaker, how Echo looks and sounds, and whether conversations are saved in Chat." },
      { target: "voice-float", optional: true, title: "Float Echo",
        body: "Opens Echo in a small window of its own, so it stays with you while you work in other apps." },
    ],
  },
  {
    id: "/recordings", page: "/recordings",
    steps: [
      { target: "page-header", title: "Record a call or a meeting",
        body: "Imprint records your microphone and, if you choose, what this computer plays. It then writes down who said what, on this computer." },
      { target: "recordings-start", optional: true, title: "New recording",
        body: "Give it a title, choose whether to include the computer's sound, and confirm that everyone knows they are being recorded." },
      { target: "recordings-list", optional: true, title: "Your recordings",
        body: "Each recording has its transcript, with speakers you can rename, and the audio to listen to or download." },
    ],
  },
  {
    id: "/reminders", page: "/reminders",
    steps: [
      { target: "reminders-new", only: "simple", title: "Set a reminder",
        body: "New sets a reminder, a timer, or a question for Imprint to answer later. You can also just ask in Chat or Echo: “remind me at 10 to call Dan”." },
      { target: "reminders-new", only: "advanced", title: "Set a reminder",
        body: "New sets a reminder, a timer, a scheduled job such as re-indexing a folder, or a question for Imprint to answer later. Chat and Echo set them too." },
      { target: "reminders-upcoming", title: "Upcoming",
        body: "What is scheduled, by day. When one rings, it shows on every page until you mark it done or snooze it." },
    ],
  },
  {
    id: "/projects", page: "/projects",
    steps: [
      { target: "page-header", only: "simple", title: "Library",
        body: "Everything Imprint remembers, sorted by project." },
      { target: "page-header", only: "advanced", title: "Projects",
        body: "Memories grouped by the project they belong to. Project associations link a source to more projects than its own." },
      { target: "library-projects", optional: true, title: "Pick a project",
        body: "The largest projects come first. The filter above the list finds one by name." },
      { target: "library-tabs", only: "simple", optional: true, title: "Overview and contents",
        body: "Overview says what the project is about. Documents and conversations lists everything in it: read one whole, or move it to the project it belongs to." },
      { target: "library-tabs", only: "advanced", optional: true, title: "Overview and Sources",
        body: "Overview shows the project's profile and facets. Sources lists its sources: read, move, link, copy to another workspace or purge them." },
    ],
  },
  {
    id: "/core", page: "/core",
    steps: [
      { target: "page-header", only: "simple", title: "About you",
        body: "What Imprint knows about you. Your AI tools are told these at the start of every session, so you do not have to repeat yourself." },
      { target: "page-header", only: "advanced", title: "Core memories",
        body: "Guidelines, which every agent session receives, and the recorded core memories they generalise." },
      { target: "core-tabs", only: "simple", optional: true, title: "Suggestions and disagreements",
        body: "Suggested holds what Imprint noticed and wants you to confirm. To settle shows newer statements that disagree with older ones." },
      { target: "core-tabs", only: "advanced", optional: true, title: "Five tabs",
        body: "Conflicts to settle, active Guidelines, Proposed drafts, Recorded core memories, and what was Retired. Every decision can be undone." },
      { target: "core-write", only: "simple", title: "Tell Imprint something",
        body: "Write it yourself: a preference, a rule or a fact about you. It is used from the next session on." },
      { target: "core-write", only: "advanced", title: "Write a guideline",
        body: "A guideline you write is active at once, for every project or for one." },
    ],
  },
  {
    id: "/skills", page: "/skills",
    steps: [
      { target: "page-header", title: "Skills",
        body: "Instructions your AI tools can follow for a kind of task, and personas they can take on. Imprint keeps them and puts them into every tool you connected." },
      { target: "skills-list", optional: true, title: "Your skills and personas",
        body: "Choose one to read it, turn it off or on, or change it." },
      { target: "skills-new", title: "New skill",
        body: "Write a new one here. It reaches every connected tool, and your paired devices." },
    ],
  },
  {
    id: "/sync", page: "/sync",
    steps: [
      { target: "page-header", only: "simple", title: "Devices",
        body: "Connect another computer so both hold the same memory." },
      { target: "page-header", only: "advanced", title: "Sync",
        body: "Share a workspace with another device. Sessions are end-to-end encrypted, through the public relay or your own." },
      { target: "devices-tabs", only: "simple", title: "Paired devices, and connecting one",
        body: "Paired devices lists the computers that stay in step by themselves. Connect a device pairs a new one: one computer shows a code, the other enters it." },
      { target: "devices-tabs", only: "advanced", title: "Paired devices, Live sync, Snapshots",
        body: "Paired devices sync by themselves. Live sync runs one session with a code. Snapshots move memory as a file, without a network." },
    ],
  },
  {
    id: "/settings", page: "/settings",
    steps: [
      { target: "page-header", only: "simple", title: "Settings",
        body: "A short list of choices in plain words. Each one is saved as you make it." },
      { target: "page-header", only: "advanced", title: "Settings",
        body: "Every setting of this device. The filter searches names, descriptions and explanations in every section." },
      { target: "settings-assistant", only: "simple", optional: true, title: "Assistant",
        body: "Where the model that answers you runs: on this computer, or at a company whose key this computer has." },
      { target: "settings-privacy", only: "simple", optional: true, title: "Privacy",
        body: "“What leaves this computer” says in plain words what is sent anywhere with the choices you have made." },
      { target: "settings-sections", only: "advanced", optional: true, title: "Sections",
        body: "One section shows at a time. A number beside a section counts the settings changed from their defaults." },
    ],
  },
  {
    id: "/organize", page: "/organize",
    steps: [
      { target: "page-header", title: "Organize",
        body: "A job that finds a project for memories sitting in catch-alls, writes a profile of each project and drafts guidelines. You review what it proposes." },
      { target: "organize-tabs", optional: true, title: "Four tabs",
        body: "To review holds the proposals that wait, Moved automatically what strong evidence moved by itself, Decided your decisions, and Profiles what each project is." },
    ],
  },
  {
    id: "/graph", page: "/graph",
    steps: [
      { target: "page-header", title: "Graph",
        body: "Projects, topics, sources and chunks as a graph. Click selects a node, double-click opens it, and the box here finds or searches." },
      { target: "graph-toolbar", optional: true, title: "Views and layout",
        body: "Switch between the graph and the star view, zoom, fit everything in the window, and change how the graph is laid out." },
    ],
  },
  {
    id: "/topics", page: "/topics",
    steps: [
      { target: "page-header", title: "Topics",
        body: "Every topic the tagger found, with the conversations and files it came from." },
      { target: "topics-list", optional: true, title: "Pick a topic",
        body: "Search or sort the list, then choose a topic to read its sources, move them or copy them to another workspace." },
    ],
  },
  {
    id: "/sources", page: "/sources",
    steps: [
      { target: "page-header", title: "Sources",
        body: "Every conversation, file, document and web page in this workspace, whatever its project." },
      { target: "sources-tabs", optional: true, title: "Indexed, Excluded, Roots",
        body: "Indexed lists what is in memory. Excluded lists what a purge keeps out. Roots are the folders this workspace may read from." },
    ],
  },
  {
    id: "/queue", page: "/queue",
    steps: [
      { target: "page-header", title: "Queue",
        body: "Imprint runs one job at a time. This page shows the running job, what waits, the background work that fills idle time, and the history with each job's output." },
    ],
  },
  {
    id: "/agents", page: "/agents",
    steps: [
      { target: "page-header", title: "Agents",
        body: "Every AI client that uses Imprint, as sessions: which run now, what they called and what failed." },
    ],
  },
  {
    id: "/commands", page: "/commands",
    steps: [
      { target: "page-header", title: "Commands",
        body: "The command line's commands as forms. Run queues one and its output appears beside it." },
      { target: "commands-list", optional: true, title: "Pick a command",
        body: "Index adds and refreshes sources, Maintain retags and scrubs, Clients connects your AI tools, Inspect reports status." },
    ],
  },
];

/** The tour of the page at a path. */
export function tourAt(pathname: string): Tour | undefined {
  const path = pathname.replace(/\/+$/, "") || "/";
  return TOURS.find((tour) => tour.page === path);
}

/** A tour's steps in one dashboard. */
export function stepsIn(tour: Tour, simple: boolean): TourStep[] {
  return tour.steps.filter((step) => !step.only || step.only === (simple ? "simple" : "advanced"));
}
