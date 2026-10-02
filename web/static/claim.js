// An extracted claim keeps its subject, predicate and object in metadata. Its
// content is the source passage, which every claim from one message can share,
// so the claim is what tells such records apart.

export function claimParts(node) {
  const meta = node?.metadata || {};
  if (!meta.subject || !meta.predicate) return null;
  // A pronoun bound to the turn's speaker reads as "Dana (I)".
  const bound = meta.speaker_reference?.fields || [];
  const named = (field, value) =>
    bound.includes(field) && value
      ? `${meta.speaker_reference.speaker} (${value})`
      : value;
  return {
    subject: named("subject", String(meta.subject)),
    predicate: String(meta.predicate).replaceAll("_", " "),
    object: named("object", meta.object == null ? "" : String(meta.object)),
    negative: meta.polarity === "negative",
  };
}

export function claimText(node) {
  const claim = claimParts(node);
  if (!claim) return null;
  const predicate = `${claim.negative ? "not " : ""}${claim.predicate}`;
  return [claim.subject, predicate, claim.object].filter(Boolean).join(" → ");
}

// A short name for a node: its claim when it has one, otherwise its content.
export const nodeLabel = (node) =>
  claimText(node) || node?.content || "(Empty content)";

// Words the extractor rewrote to make the text stand alone (#91), as "I → Dana".
export function rewrites(node) {
  const replacements = node?.metadata?.resolution?.replacements;
  return Array.isArray(replacements)
    ? replacements.map((item) => `${item.original} → ${item.replacement}`)
    : [];
}
