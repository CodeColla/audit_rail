# audit_rail
This Repo is for Enterprise Grade Compliance/Audit Team to keep track of Policies, Documents, Proof, etc. More about it in the md FIle.


# Problem Statmenet.
Now my company is catering to a lot of banks in India, due to which iam considered as their Vendor. According to RBI i have to be compliant with them, as per their points as i provide E-Surviallance as a Service to them.
Now in this Service i provde IOT Solutions to the Banks for their HO's, Branches, Gold Loans, Currency Chests etc. For now in IOT i have mentioned Intrusiin Panel and CCTV monitoring, and thier Helath Check. Via Intrusion my
team (CMS) looks at alerts via my Sentrix Portal.

Now just for this Service, since last year a lot of Banks like ICICI, HDFC, KOTAK, MF , Bajaj Finance have started coming to me for audits, and their Audit Teams usually from PWC, Dellote also come for the Verification. In this Audit info from HR, to Technical Details like Firewall, Network , to Application is looked in to.

Now i have an intern only who does this Documentation, of Policy Gathering, and details gathering. But it is always a last moment thing when doc collections starts moving, we have had audit 2times till now, and have found out that it is acutllay good for us to maintain the standard practices, secutiry wise as well. 

Now the issue is some of these are 1 time thing which once implimented no need to look for, but some are Repeated or should be repeated Quaterly or monthly etc. Which is not done.
In some cases we have to keep it as a standard Practice in the compliance dept to make sure the person in this dept, comes up and fills in the  doc, policu and evidance according to it. 

Now this is more like SOC2 i would say, as we had it once , but the documentation was so heavy and the portal we used for it was scrut was very expensive.

Now we already have a decent amount of checklist sheet form all these audits, and in phase0 want to start with the planning of building a Inhouse portal first
that will be used by our compliance team. 

Then later on we can make it maybe into some standard product.


# How to Proceed
@ PHASE0
- First i want u to undertsand the doc/sheet iget . in which points are mentioned, and i have to make evidance/policies ready according to it.
- We will understand it commonly first. In this case we will be planning and u have to consider uyrslef as an audit expert/compliance expert as well as cybersecurity expert.


@ PHASE1
- Once we have understood the points, we will plan it in a Requirement, assuming we will be Developing a product out of this. 
- In this phase the agenda will be how to make it a multi tenant (company), whose compliance teeams will use this product, not just compliance bu the auditor can also use it to give remarks on the points,so accordingly now modules we have to finalise here, and their table shema in SQL.


@ PHASE2
- Based on the Schema we will go along with the mono repo structure like we have in the `/home/sumit/SR/emp_erp_mcp` repo. And plan the developement.
Tech Stack will be :
Pyhton (FastAPI)
SQLITE3 (for intial phase then move to PSQL maybe or ur call)
ReactJS using schadcn in UI.

Now this tool will be under the  so refer the SR these for this more about the theme in this file  `/home/sumit/PORTFOLIO/SR/02-Brand-Website-LinkedIn/Logo-and-Brand-Brief.md`. 

The excels are in the Data Folder. 

Let me know if u need  more info or have questions.

Initially i want to make it use inhouse in IAM powered by SR and then i might make it as a product.