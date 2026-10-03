# Template Resolution Specification

**Provenance.** This is the normative specification for
`persona_grata/template_engine.py`, published here so the citations in that module and
its tests resolve for anyone reading the repository. The canon copy
(`~/canon/workbook/spec/TEMPLATE_RESOLUTION.md`) remains the source of truth; this file
is a mirror, and a change belongs in the canon first.

**How to cite a rule.** Rules are numbered `0`–`7`, with sub-rules lettered `a`, `b`,
… The numbering is the contract — code comments refer to rules as "rule 3b" — so
renumbering or inserting a rule is a breaking change to those references. Cite the rule
number and letter, never a line number: rule text moves as the document grows, and a
line pointer silently starts pointing at the wrong rule.

## Rules:
##
## 0. For any class of variable, resolution is left to right.
##   a. The first identifier match from left to right is evaluated first.
##   b. If left-right match cannot be resolved, evaluation is delayed until the next identified.
##   c. If the identifier cannot be resolved in any way, resolution fails (error)
##
## 1. Environment variables (`$FOO` or `${FOO}`) are evaluated **FIRST** as macros.
##   a. They are direct substitutions. Both spellings mean the same thing; the braced form
##      exists to end the name where the surrounding text would not.
##   b. They should be processed on raw text **before** parsing, if possible.
##   c. File parsing should start only after env. variable substutitons are completed.
##   d. Environment variables resolution is conducted in ONE pass only, with no looping.
##   e. Non-existent environment variables resolve to the empty string if unmatched.
##   f. `$$` is the escape for a literal `$`; since substitution is single-pass (1d), the
##      `$` it produces is never re-scanned, so `$$FOO` yields the text `$FOO`.
##   g. A name is `[A-Za-z_][A-Za-z0-9_]*`. It may NOT begin with a digit, so `$1` and `$9X`
##      are literal text even when a variable of that name exists -- this is a grammar
##      decision, not a lookup miss. Stated because the obvious reading of "environment
##      variable" admits `$1`, which a shell reader takes for a positional argument.
##
## 2. Reserved identifiers are resolved AFTER environment variables, but BEFORE regular identifiers.
##   a. They cannot be used as regular identifiers or shadowed.
##   b. Their meaning is fixed:
##     i. "self" --> the current K/V pair
##    ii. __PARENT__ --> the specified element's parent, or if unqualified, that of the current K/V pair
##   iii. __KEY__ --> The specified element's key, or if unqualified, that of the current K/V pair
##   c. Reserved identifiers are resolved by repeated passes until completely resolved or until no further resolution is possible.
##
## 3. Individual identifiers are resolved by checking for matches in this order:
##   a. reserved identifiers
##   b. absolute identifiers (i.e., if "foo" matches root identifier & sibling, root identifier takes precedence
##   c. local (sibling) identifiers
##   d. ancestral identifiers (parent, grandparent, etc.)
##   e. ancestral sibling identifiers (i.e., "[great ]*{uncle|aunt}")
##   NOTE: steps d-e are interleaved per level, nearest first. At each ancestor
##         (nearest first) check the ancestor's own key (d), then that ancestor's
##         siblings (e), before climbing further. Thus a near uncle beats a far
##         ancestor. Cousins (an uncle's children) are never matched by a bare
##         identifier; reach them by explicit qualification (e.g. {{uncle.cousin}}).
##
## 4. Templates may appear in dict KEYS as well as values.
##   a. A templated key is resolved as if it were a node at its own position, then renamed in place.
##   b. Keys are resolved before values in each pass, so a value may refer to the final key name.
##   c. Insertion order is preserved; a rename colliding with an existing key fails (error).
##
## 5. A reference may end in a terminal serializer call, which renders the subtree the chain
##    reached instead of resolving to a scalar.
##   a. `__AS_JSON__()` --> indented JSON; `__AS_TOML__()` --> TOML, nesting maps into tables;
##      `__AS_YAML__()` --> block-style YAML, keys in declaration order rather than sorted.
##   b. The call MUST be the last segment of the reference.
##   c. Empty/unset entries (None, "", and containers emptied by pruning) are dropped; falsey
##      scalars (0, false) are kept.
##   d. A subtree still holding `{{}}` defers (rule 0b), so serialization sees resolved data only.
##
## 6. Any segment may carry `[...]` subscripts, which index by a literal position or by
##    another reference.
##   a. A subscript written as a bare non-negative integer IS the index: `{{rows[1]}}` is
##      position 1. Any other subscript is a reference, resolved in the scope of the node
##      HOLDING the template, not of the node being indexed -- so
##      `{{mind.dialects[protocol].api_uri}}` means "the entry of mind.dialects named by *my*
##      protocol". Nothing is shadowed by the literal case: an identifier made entirely of
##      digits cannot be written as one.
##   b. Subscripts chain (`{{m[a][b]}}`) and traversal continues after them. The two forms mix
##      freely -- `{{rows[1][j]}}` is a literal index then a referenced one.
##   c. A mapping is indexed by key; a list by position. A referenced subscript is coerced to an
##      int for a list (so "2" and 2 both work) and a value that will not coerce fails.
##   d. An entry that is not there fails (rule 0c). A subscript whose own reference resolves to
##      something still holding `{{}}` defers (rule 0b) rather than indexing by the literal text --
##      "not yet" and "not there" are different answers, and only the first waits.
##
## 7. A reference may instead BE a function call, `__NAME__(arg, ...)`.
##   a. Unlike a serializer (rule 5), the call is the WHOLE reference: it produces a value rather
##      than terminating a chain, so nothing may precede or follow it.
##   b. Arguments are references, resolved in the current scope, and MAY resolve to containers --
##      the one place that is allowed. Everywhere else a reference must reach a scalar.
##   c. `__MATCH_FIRST__(key_set, items)` --> the first member of `key_set` (a list, read as a
##      preference order) that appears among the keys of `items` (a mapping). It yields the KEY,
##      which composes with rule 6 to reach the value:
##
##          protocol: "{{__MATCH_FIRST__(supported_dialects, mind.dialects)}}"
##          base_url: "{{mind.dialects[protocol].api_uri}}"
##
##   d. No match fails (rule 0c), naming both what was wanted and what was on offer.
##
## Examples: Let the environment hold `FOO="$BAR"; BAR="Hello!"
##
##    foo:
##      tek: "$FOO"                               tek -> `$BAR` - four (4) characters ("$BAR"), _not_ six (6) characters ("Hello!").
##      sof: "$BIZZLE"                            resolves to `sof: `.
##      bar: {{__PARENT__.__KEY__}}               __PARENT__ -> foo; foo.__KEY__ -> `foo`
##      baz:
##        tek: {{__PARENT__.__PARENT__.bar}}/daf  __PARENT__ -> baz; baz.__PARENT__ -> foo; foo.bar -> `foo`; thus, `foo/daf`
##        zod: {{baz.tek}}/zod                    baz resolves to ancestor (foo.baz); foo.baz.tek -> `foo/daf`; thus, `foo/daf/zod`
##        viq: {{tek}}/viq                        tek resolves to sibling (not uncle); hence, `foo/daf/viq`
##        wel: {{bar}}/wel                        bar resolves to "uncle"; thus, `foo/wel`
##      yot: {{baz.tek}}/yot                      baz resolves to sibling (foo.baz); foo.baz.tek -> `foo/daf`; thus, `foo/daf/yot`
##      buz: {{self.__PARENT__.yot}}/buz          self -> foo.buz; foo.buz.__PARENT__ -> foo; foo.yot -> `foo/daf/yot`; thus, `foo/daf/yot/buz`
