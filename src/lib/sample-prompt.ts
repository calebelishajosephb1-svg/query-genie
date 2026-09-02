export const SAMPLE_PROMPT = `Build a complete restaurant chain database and then interrogate it end to end.

STRUCTURE: design it yourself — branches, customers, employees, dining tables, categories, menu items, orders, order items, payments, reservations, suppliers, ingredients, menu-ingredient links and supplier-ingredient links, plus anything else logically required. Give everything proper identifiers, primary/foreign keys, sensible constraints, and make the relationships actually make sense.

SEED DATA: a decent amount of realistic data — multiple branches, customers, employees, categories, menu items, reservations, orders, payments, suppliers and ingredients — deliberately including records with no matching rows on the other side (a menu item never ordered, a customer with only a reservation, a branch with no qualifying orders, a supplier whose ingredients aren't needed, NULLs and ties) so missing-side behaviour can be tested. Then show everything you created.

QUERIES (each answer feeds the next):
1. All customers, then only customers who spent above the overall customer average.
2. From only those customers: their orders, then those orders' order items, then the menu items in those items, then the categories of those menu items with money contribution, then only categories above the average category total.
3. Employees who handled orders in those categories, ranked inside each branch by business handled, plus their % of branch total and the difference from the previous ranked employee.
4. All branches, then branches whose average order value beats the overall branch average, where the threshold itself comes from a derived result over completed+paid orders only.
5. All reservations, then customers who never completed an order but do have a reservation, with their reservation and reserved branch, while still keeping branches that have no such customers.
6. All menu items, then menu items never ordered that belong to categories whose average price is above the overall menu average.
7. Per-branch summary: order count, unique customers, total revenue, average order value, highest order, lowest order, average customer spend, % of total restaurant revenue, plus a final overall total row.
8. All menu items with category info and revenue, then the top 3 items per category compared against the category average, with the difference.
9. Ingredients used by those highest-revenue items, the suppliers of those ingredients, supplier quantities, the largest-supplying supplier and comparison against the average supplier quantity.
10. Customers who outspend every customer of at least one other branch, and separately customers who outspend every other customer.

CHANGES (derive the affected rows from earlier results, no hardcoded IDs), re-selecting affected rows after each one:
- raise prices of menu items in the highest-revenue category
- raise salary for employees ranked first in their branch
- change status of selected reservations
- update a supplier
- modify one order amount

DELETES with verification selects after each: delete an order handling all dependents correctly, delete a menu item only if never ordered, delete a supplier only if none of its ingredients are required.

RECOMPUTE everything that may now be stale: branches, their employees, employee revenue handled, employee rank in branch, branch salary averages, customers served, customer total spend, most expensive order, order count, favourite category by spend, highest-revenue item purchased; alongside branch top-3 items, category totals, supplier info for ingredients of those items, reservation counts, unpaid/completed/cancelled order counts and revenue contribution — but only for branches whose revenue is above the branch average AND whose employee salary average is below the overall salary average, while still preserving a branch that has qualifying reservations but no qualifying orders. In the same pass also give: top-spending customer per branch, highest-revenue menu item per category, employee with most orders overall, supplier with the greatest ingredient quantity, branch with the highest reservation-to-order ratio, and the category with the greatest increase after the price changes — reusing earlier derived results wherever possible, and returning all intermediate outputs plus the final report.

Handle NULL / missing / duplicate / tied / empty / no-matching-record cases explicitly rather than dropping them, and explain the result through the output itself (labels, flags, comments).

FINALLY, after everything, SELECT * from every single table one by one so the output shows the complete final state — do not skip any table even if it is empty.`;
