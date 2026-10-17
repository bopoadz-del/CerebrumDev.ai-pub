# Coder protocol: no hardwiring, and how to read a failing check

## The one rule (the owner's, verbatim — it governs everything below)

> NO HARDWIRING. A failing case is never fixed by name. No per-case branches,
> no literal document strings, no test-case IDs, no rescue functions, no
> per-case switches. Find the general cause and fix the mechanism so every
> case of that kind passes. If you cannot find a general fix, say so and stop
> — a pass patch is a failure, not progress.

The gate has teeth: a fix is accepted only if it holds on inputs you have
never seen. CI rejects a diff that adds `_rescue_*`, `*_NEEDLES`, `*_RESCUE`
knobs, or a probe or test-case ID inside product code. "Loop until every
test passes" is not your order; passing the tests you can see is not the
target — working on the ones you cannot see is.

## Four tests for whether what you wrote is variable by design

1. **No named cases in production code.** If a probe ID, a test-case name, a
   specific document string, or one customer's values appears in the shipped
   path, it is a fixed point wearing a variable-shaped wrapper.
2. **It passes on inputs it has never seen — by construction, not by luck.**
   If the only cases it clears are the ones you tuned on, the variability is
   an illusion.
3. **Every fix is general, or it is rejected.** A change that makes one case
   pass and cannot be justified as a class-level fix does not ship.
4. **Contracts, not answers.** A capability declares what it accepts and the
   shape it produces — on the spec (`allowed_values`, types, bounds) — and
   never the specific values it expects. Shape is enforced; values flow
   through.

## How to read a failing check

A failing check says the product is not yet doing what the brief asked, at
the bar the brief set. Fix the cause. Never make the test, or the product's
own validation, agree to accept wrong input.

1. Read the `FAILED` line and its message. A vocabulary error names the field
   and the values the route accepts (`... must be one of: a, b, c`).
2. Decide which side is wrong:
   - The route is **right** to refuse a value outside its declared vocabulary.
     The fix is to declare that vocabulary on the spec field so a payload built
     from the spec is one the route accepts — not to make the route accept
     anything.
   - The route is **wrong** only if it refuses a value the brief defines as
     valid. Then fix the handler, and keep every other check.
3. If the message says the tester **already tried the values the route named**
   and the route still refused, the product contradicts itself: two layers
   (route guard, handler, block) disagree about the same field. Reconcile them.
   Do not delete either blindly.
4. If the general cause is not there to be found, say so and stop. That is a
   valid outcome. A patch is not.

## The bar is the brief's, not a fixed one

Never weaken, delete, or loosen a validation, a route check, or a test
assertion **below the bar the brief set**. If the brief asked for a prototype,
prototype-level checks are the bar, and meeting it is not cheating; if the
brief asked for pilot, pilot is the bar. What is never allowed is buying a
green by removing a check the brief required — that is a red you hid, and the
next gate finds it.

## What counts as a pass patch — do none of these

- Deleting or commenting out a validation branch so bad input passes.
- Widening an enum, or dropping it, to swallow a test's placeholder value.
- Replacing a real assertion with `assert True`, `pytest.skip`, or `xfail`.
- Catching an exception and returning `ok: true` to mask it.
- Hardcoding a test's expected output inside the handler.
- A branch, a string match, or a lookup keyed on one case so that case passes.

## What a real fix looks like

- The contract on the spec matches what the handler enforces, so a payload
  built from the spec is one the handler accepts.
- The handler does the work the brief describes and returns real results, for
  any valid input of that kind — not for the inputs you happened to see.
- Every check the brief required is still there after the fix.
