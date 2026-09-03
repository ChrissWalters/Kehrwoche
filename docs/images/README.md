# Screenshots

The images in this folder are **placeholders**. They are the right size and carry the
right file names, so replacing them is a matter of dropping the real pictures in — no
markdown has to change.

## What to capture

| File | View | Viewport | Notes |
|---|---|---|---|
| `mobile-chores.png` | Chores, list | 412 × 915 | Three or four chores, one of them overdue, avatars visible |
| `mobile-shopping.png` | Shopping list | 412 × 915 | A few open items, one or two ticked off |
| `mobile-expenses.png` | Expenses, balances | 412 × 915 | Balances with one person in credit and one in debt |
| `desktop-feed.png` | Pinboard | 1280 × 800 | Wide layout with the sidebar, a mix of system entries and a post |

**Capture at a device pixel ratio of 2**, so the files come out at twice those numbers
(824 × 1830 and 2560 × 1600) and stay sharp on a high-resolution display.

## Before you shoot

* **Use a household with plausible content.** Two or three people, real-sounding chores,
  a handful of expenses. An empty instance sells nothing, and made-up names are safer than
  real ones.
* **Light theme** for all four, so they sit together on the page.
* **Mobile shots at 412 × 915**, which is what a current phone actually shows. Not to be
  confused with the 360 px design floor from the conventions: that is the narrowest
  viewport the interface has to survive, not the one people look at.
* **No personal data**: no real surnames, no real email addresses, no join code that is
  still valid.

## How the placeholders were made

`tools/make_placeholders.py` draws them. Running it again overwrites the files, so do not
run it after the real screenshots are in place.
