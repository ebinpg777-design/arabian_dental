# Part of Ebshel AI Suite. See LICENSE file for full copyright and licensing details.
{
    'name': 'Ebshel AI Suite - Live Chat',
    'version': '19.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Let an Ebshel AI assistant answer website live chat visitors first',
    'description': """
An AI assistant answers live chat visitors from its knowledge sources and hands
the conversation over to a human operator when it cannot help, when the visitor
asks for a person, or after a configurable number of answers.
""",
    'author': 'Ebshel Technologies',
    'website': 'https://ebshel.com',
    'license': 'LGPL-3',
    'depends': ['ebshel_ai_suite', 'im_livechat'],
    'data': [
        'views/im_livechat_channel_views.xml',
    ],
    'auto_install': True,
    'installable': True,
}
