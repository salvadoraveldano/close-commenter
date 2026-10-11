# Hackaton Idea: Finance Commenter AI Agent


## Goal 
An AI oriented agentic app that explores our month-end activity, helps us find annomalies, significant variances, and helps us complete our month-end close final commentary to use in month-end presentations, board packages, CEO-grade, and investors material.


## Problem

There can be many AI approaches to close the month, but I feel that regardless of any approach, we need a way to keep this accounting/finance activity human revised and approved. To tackle this problem, my idea consist of using the closed transactional activity of a company to proceed to generate the most critical part, the revision-by-a-human component. 

Regardless of how sophisticated the close and transaction classification, GL closer, etc... we need a part of the process that ensures a human has reviewed the full close and has a simple, easy-to-read and top tier UI for revision that an accountant, CFO, auditor, business owner, or practically any professional in the org can do. 

## Components

Assume we have our transactional financial activity for the month close—we will need to generate dummy data—this tool will allow us to:

- explore the full transactions of a company
- be able to filter for the actual transactions that occurred in the period vs. the accounting-focused type one, such as accruals and other accounting-only type o transactions
- have an extraordinary and easy-to-use UI that helps the user to be able to approve or further review any transaction
- have a smart filtering and revision component that helps us detect annomalies, suspicious or fraudulent transactions 
- have a final section where the AI suggest what to include in a final financial closing presentation where it smartly suggest the top metrics and its insights but allows the user to write their final sections for the presentation. This is critical because it forces the user to read and understand what happened so that it can actually explain with its words. At the end the AI can enhance the writing but this part is so critical because ensures the humans and responsibles in the company are actually understanding what they're saying and not only usign AI to fully do a closing.
- It must allow to redirect a transaction or transactions to someone else for: a second opinion, further investigation, clarification, and approval. 



## Success critieria


- I can log in with a simulated user and password to showcase this at the hackaton
- I can view the transactions pending to review and approve
- I have a way to approve in batch when there are many transactions and pass a safety check defined in this process by the AI coding agent
- I have a chance to explore relevant transactions that the AI is suggesting me to check
- To have a section where I can see the insights for the presentation as suggested by the AI agent
	- this section should show: 
		* What happened
		* Historical context and stats: i.e., the spend for this category or service has always been at this amount or spend for this is 5x the historical and the contact is new, needs your revision
		* suggested ways to show this component and which are the best charts or visualizations for a presentation package. 
		* the insights for the final presentation should be high-level, let's avoid showing a transaction for coffee expenses in a final presentation just because it has high-variance. Instead you could suggest the bigger picture, such as: admin and offic expenses are going up because headcount and our office occupancy has also increased.
		* have a way to mark or export the insight for the final presentation material section where all the consolidated final insights are located.
- Have a complete prototype to show at the hackaton with the dummy information completed to also use in this app that is compatible with the required format to show a very interesting working demo that has the Wow Factor required for the hackaton.


## Resources

- For the agentic uses let's connect it to our claude code session but be compatible to use with Anthropic API keys or Gemini Keys.

## Skills to use

/ponytail  -- for coding best practices
/taste     -- for the UI and app design

