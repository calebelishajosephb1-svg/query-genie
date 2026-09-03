# Query Genie

I want you to create a website that converts nl to sql queries...it should be able to convert to any of these engines the user chooses...

postgresql

mysql

mariadb

mssql

oracle

sqlite

db2

snowflake

aurora

access

the query can be like this...
inside it start by making a proper system for a restaurant chain, but dont expect me to say everything neatly lol /??1@... i want you to decide the structure yourself and make whatever related sets of information are needed for customers, staff, branches, tables, menu items, categories, orders, order items, payments, reservations, suppliers, ingredients and whatever else is logically required... give each thing proper identifiers and connect them wherever one thing depends on another, and make sure the relationships actually make sense because later im going to make you use basically all of them... once the structure is ready, put in a decent amount of realistic sample information with multiple branches, customers, employees, menu items, categories, reservations, orders, payments, suppliers and ingredients, including some things that intentionally have no matching records so we can test missing-side behaviour... after that show me what you created and then start querying it but dont do simple questions one after another, i want each answer to feed the next one... first select all the customers and then select only the customers who have spent above the overall customer average, then from ONLY those customers select their orders, then select the order details belonging to those orders, then select the menu items appearing in those order details, then select the categories those menu items belong to and determine which categories contribute the most money, then select those categories whose totals are above the average category total /??1@... now select the employees who handled orders belonging to those categories and rank them inside each branch by the amount of business they handled, but also select their percentage of their branch total and the difference from the previous ranked employee... then select all branches and after that select only branches whose average order value is greater than the overall branch average, but the threshold for that comparison should itself come from a derived result involving only completed paid orders... now select all reservations and then select the customers who have never made a completed order but DO have a reservation and show their reservation information together with the branch they reserved at, while still keeping branches that currently have no such customers... then select all menu items and after that select menu items that have never appeared in an order but belong to categories whose average price is above the overall menu average... now select every branch with the number of orders, unique customers, total revenue, average order value, highest order, lowest order, average customer spend, and percentage that branch contributes to total restaurant revenue, and add a final overall result... then select all menu items with their category information and calculate revenue, then select the top 3 menu items inside every category and compare their revenue against the category average, and select the difference... now select the ingredients used by those highest-revenue menu items, then select the suppliers connected to those ingredients, then select supplier quantities and determine which supplier supplies the largest total quantity of those ingredients and compare that against the average supplier quantity... then select customers whose total spending is higher than the spending of every customer from at least one other branch, and separately select customers whose spending is higher than every customer overall except themselves... /??1@#... now change some things: increase the price of menu items belonging to the highest-revenue category, give a salary increase to employees who rank first in their branch, change the status of selected reservations, update a supplier, and modify one order amount, but calculate the affected records from the earlier results instead of me telling you their IDs... after every change select the affected records again so i can see exactly what changed... then remove an order and make sure all dependent information is handled correctly, remove a menu item only if it has never been ordered, and remove a supplier only if none of its ingredients are currently required... after each removal select the related records again and verify that nothing invalid has been left behind... then redo the important calculations because the earlier answers may now be stale... select all branches again, then select their employees, then select employee revenue handled, then select employee rankings within each branch, then select branch salary averages, then select customers served by those employees, then select each customer's total spending, then select their most expensive order, then select their number of orders, then select their favourite category based on spending, and then select the highest-revenue menu item they purchased... alongside that select the branch's top 3 menu items, select category totals, select supplier information for ingredients used by those items, select reservation counts, select unpaid completed and cancelled order counts, and select revenue contribution... BUT only include branches whose revenue is above the average revenue of all branches AND whose employee salary average is below the overall employee salary average, while still preserving the branch if it has qualifying reservations but no qualifying orders... and somewhere inside the same request also select the customer with the highest spending in each branch, select the menu item with the highest revenue in each category, select the employee who has handled the most orders overall, select the supplier connected to the greatest ingredient quantity, select the branch with the highest reservation-to-order ratio, and select the category with the greatest increase after the price changes... dont calculate these independently, use the earlier derived results wherever possible, and return all the intermediate outputs as well as the final massive report... if something is NULL, missing, duplicated, tied, empty, or has no matching record, dont just ignore it, handle it according to the relationship and explain the result through the output itself... and finallyyyyy after EVERYTHING is finished and all updates deletes comparisons rankings nested results and intermediate selects are done, select ALL information from ALL the tables one by one so the final output gives me the complete final state of every single table in the restaurant system /??!!1@# /!\ 2?? and dont skip any table even if it is empty or has no matching records pls xD


and the AI (btw will be using API keys for the AI model to be used) will convert it into SQL, based on what engine the user asks for (mysql, sqlite, oracle, etc...)

like for instance, chatgpt answered like this (main.txt.)

I want the AI to think carefully of the nl query and go thru it completely and thoroughly, before giving the final txt file qeury sheet....

This project was built with [Lovable](https://lovable.dev).

## Build with Lovable

Continue developing this project in the [Lovable editor](https://lovable.dev/projects/ca0f0455-d233-4970-b235-ed923e90de12).

- **Ship faster**: describe what you want to build and Lovable handles the code.
- **Stay in sync**: every change made in Lovable is committed straight to this repository.
- **Full ownership**: this code is yours. Push to `main` on GitHub and your changes sync back into Lovable, ready for your next prompt.

## Development

Prefer working locally? You need Node.js and npm — [install with nvm](https://github.com/nvm-sh/nvm#installing-and-updating).

```sh
git clone <this-repository-url>
cd <repository-name>
npm i
npm run dev
```
